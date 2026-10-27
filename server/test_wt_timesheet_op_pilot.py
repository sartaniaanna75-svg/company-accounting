# -*- coding: utf-8 -*-
"""
Этап 11 — пилот точечного сохранения ячейки табеля через POST /api/state/op.

Без PostgreSQL и без Timeweb: FakeConn + разбор app.html.
Запуск из каталога server:
  python test_wt_timesheet_op_pilot.py
"""

from __future__ import annotations

import json
import os
import re
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import wt_api
from test_wt_api import SAMPLE_APP, FakeConn, SECRET, call, cookie_from

APP_HTML = Path(__file__).resolve().parent.parent / "app.html"

CELL_A = {"status": "Я", "hours": 8, "lateMinutes": 0}
CELL_B = {"status": "О", "hours": 8, "lateMinutes": 10}
CELL_C = {"status": "В", "hours": "", "lateMinutes": 0}


class WtTimesheetOpPilotTests(unittest.TestCase):
    """Сценарии пилота табеля на FakeConn (рабочая БД не трогается)."""

    def setUp(self):
        self._env_backup = dict(os.environ)
        os.environ["DATABASE_URL"] = "postgresql://worktime:" + SECRET + "@127.0.0.1:5432/worktime"
        os.environ["WT_PIN_PEPPER"] = "unit-pepper"
        os.environ["WT_SCRYPT_N"] = "16"
        os.environ["WT_OPS_ENABLED"] = "1"
        os.environ.pop("WT_API_TOKEN", None)
        os.environ.pop("WT_COOKIE_SECURE", None)
        wt_api.LOGIN_FAILS.clear()
        payload = json.loads(json.dumps(SAMPLE_APP))
        # Реальная структура ячейки — объект (как в UI), не скаляр.
        payload["workspaces"][0]["timesheet"] = {
            "2026-10": {"e_shop": {"1": dict(CELL_A), "2": dict(CELL_A)}}
        }
        payload["workspaces"][1]["timesheet"] = {
            "2026-10": {"e_wh": {"1": dict(CELL_A), "2": dict(CELL_A)}}
        }
        self.store = {
            "payload": payload,
            "version": 3,
            "updated_at": datetime.now(timezone.utc),
            "updated_by": "",
            "fail": False,
            "sessions": {},
            "lock": threading.Lock(),
        }
        self._patch = patch.object(wt_api, "connect", side_effect=lambda: FakeConn(self.store))
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        os.environ.clear()
        os.environ.update(self._env_backup)
        wt_api.LOGIN_FAILS.clear()

    def login(self, user_id="user_admin", pin="5555"):
        status, data, _, cookies = call("POST", "/api/login", {"user_id": user_id, "pin": pin})
        self.assertTrue(status.startswith("200"), status)
        return cookie_from(cookies), data

    def op(self, cookie, path, value, expected_v=0, idem=None):
        body = {"op": {"path": path, "expected_v": expected_v, "value": value}}
        if idem:
            body["idempotency_key"] = idem
        return call("POST", "/api/state/op", body, cookie=cookie)

    def test_path_shape_matches_ui_helper(self):
        """Путь записи совпадает с wtTimesheetDayPath в app.html."""
        src = APP_HTML.read_text(encoding="utf-8")
        self.assertIn("function wtTimesheetDayPath(", src)
        self.assertIn('return "workspaces." + String(deptCode)', src)
        self.assertIn('".timesheet." + String(month)', src)
        path = "workspaces.shop.timesheet.2026-10.e_shop.3"
        self.assertEqual(wt_api.normalize_op_path(path), path)
        st, data, *_ = self.op(
            self.login()[0],
            path,
            CELL_B,
            expected_v=0,
        )
        self.assertTrue(st.startswith("200"), (st, data))
        shop = next(w for w in self.store["payload"]["workspaces"] if w["code"] == "shop")
        self.assertEqual(shop["timesheet"]["2026-10"]["e_shop"]["3"], CELL_B)
        # Список workspaces не превратился в dict.
        self.assertIsInstance(self.store["payload"]["workspaces"], list)

    def test_two_users_different_cells(self):
        admin_c, _ = self.login("user_admin", "5555")
        staff_c, _ = self.login("usr_001", "1111")
        results = {}

        def admin_job():
            st, d, *_ = self.op(
                admin_c,
                "workspaces.shop.timesheet.2026-10.e_shop.1",
                CELL_B,
                expected_v=0,
                idem="pilot-a-day1",
            )
            results["admin"] = (st, d)

        def staff_job():
            st, d, *_ = self.op(
                staff_c,
                "workspaces.shop.timesheet.2026-10.e_shop.2",
                CELL_C,
                expected_v=0,
                idem="pilot-b-day2",
            )
            results["staff"] = (st, d)

        t1 = threading.Thread(target=admin_job)
        t2 = threading.Thread(target=staff_job)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        self.assertTrue(results["admin"][0].startswith("200"), results["admin"])
        self.assertTrue(results["staff"][0].startswith("200"), results["staff"])
        shop = next(w for w in self.store["payload"]["workspaces"] if w["code"] == "shop")
        self.assertEqual(shop["timesheet"]["2026-10"]["e_shop"]["1"], CELL_B)
        self.assertEqual(shop["timesheet"]["2026-10"]["e_shop"]["2"], CELL_C)

    def test_two_users_same_cell_conflict(self):
        admin_c, _ = self.login("user_admin", "5555")
        staff_c, _ = self.login("usr_001", "1111")
        path = "workspaces.shop.timesheet.2026-10.e_shop.1"
        st1, d1, *_ = self.op(admin_c, path, CELL_B, expected_v=0, idem="same-1")
        self.assertTrue(st1.startswith("200"), (st1, d1))
        st2, d2, *_ = self.op(staff_c, path, CELL_C, expected_v=0, idem="same-2")
        self.assertTrue(st2.startswith("409"), (st2, d2))
        self.assertEqual(d2.get("error"), "conflict")
        self.assertEqual(d2.get("server_value"), CELL_B)
        shop = next(w for w in self.store["payload"]["workspaces"] if w["code"] == "shop")
        self.assertEqual(shop["timesheet"]["2026-10"]["e_shop"]["1"], CELL_B)

    def test_idempotent_retry(self):
        cookie, _ = self.login("usr_001", "1111")
        path = "workspaces.shop.timesheet.2026-10.e_shop.2"
        body_key = "pilot-retry-cell"
        st1, d1, *_ = self.op(cookie, path, CELL_B, expected_v=0, idem=body_key)
        ver = int(self.store["version"])
        rv = int(d1.get("record_v") or 0)
        st2, d2, *_ = self.op(cookie, path, CELL_B, expected_v=0, idem=body_key)
        self.assertTrue(st1.startswith("200"), st1)
        self.assertTrue(st2.startswith("200"), st2)
        self.assertEqual(d2.get("value"), CELL_B)
        self.assertEqual(int(self.store["version"]), ver)
        self.assertEqual(int(d2.get("record_v") or 0), rv)
        shop = next(w for w in self.store["payload"]["workspaces"] if w["code"] == "shop")
        self.assertEqual(shop["timesheet"]["2026-10"]["e_shop"]["2"], CELL_B)

    def test_put_after_op_does_not_overwrite_other_cell(self):
        cookie, _ = self.login("user_admin", "5555")
        path = "workspaces.shop.timesheet.2026-10.e_shop.1"
        st_op, *_ = self.op(cookie, path, CELL_B, expected_v=0, idem="put-guard")
        self.assertTrue(st_op.startswith("200"), st_op)
        ver = int(self.store["version"])
        # Устаревший широкий PUT без актуального _wt_records для ячейки.
        stale_timesheet = {
            "2026-10": {
                "e_shop": {
                    "1": dict(CELL_A),  # пытается вернуть старое
                    "2": dict(CELL_C),  # новое через PUT
                }
            }
        }
        st_put, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": ver,
                "payload": {
                    "workspaces": [{"code": "shop", "timesheet": stale_timesheet}],
                    "_wt_records": {},
                },
            },
            cookie=cookie,
        )
        self.assertTrue(st_put.startswith("200"), st_put)
        shop = next(w for w in self.store["payload"]["workspaces"] if w["code"] == "shop")
        self.assertEqual(shop["timesheet"]["2026-10"]["e_shop"]["1"], CELL_B)
        self.assertEqual(shop["timesheet"]["2026-10"]["e_shop"]["2"], CELL_C)

    def test_other_workspace_and_salary_untouched(self):
        cookie, _ = self.login("usr_001", "1111")
        before_wh = json.loads(json.dumps(self.store["payload"]["workspaces"][1]))
        before_salary = json.loads(json.dumps(self.store["payload"]["salary"]))
        before_fin = json.loads(json.dumps(self.store["payload"]["financeRec"]))
        st, *_ = self.op(
            cookie,
            "workspaces.shop.timesheet.2026-10.e_shop.1",
            CELL_B,
            expected_v=0,
            idem="preserve-others",
        )
        self.assertTrue(st.startswith("200"), st)
        self.assertEqual(self.store["payload"]["workspaces"][1], before_wh)
        self.assertEqual(self.store["payload"]["salary"], before_salary)
        self.assertEqual(self.store["payload"]["financeRec"], before_fin)

    def test_warehouse_group_same_path_rules(self):
        """Та же схема пути для склада — не только для магазина Володарского."""
        cookie, _ = self.login("usr_wh", "2222")
        path = "workspaces.warehouse.timesheet.2026-10.e_wh.2"
        st, data, *_ = self.op(cookie, path, CELL_C, expected_v=0, idem="wh-pilot")
        self.assertTrue(st.startswith("200"), (st, data))
        wh = next(w for w in self.store["payload"]["workspaces"] if w["code"] == "warehouse")
        self.assertEqual(wh["timesheet"]["2026-10"]["e_wh"]["2"], CELL_C)
        self.assertEqual(wh["timesheet"]["2026-10"]["e_wh"]["1"], CELL_A)

    def test_ops_disabled_returns_503_without_changing_cell(self):
        """Имитация «ошибки / недоступности» op: значение на сервере не меняется."""
        cookie, _ = self.login("usr_001", "1111")
        with patch.dict(os.environ, {"WT_OPS_ENABLED": "0"}, clear=False):
            st, data, *_ = self.op(
                cookie,
                "workspaces.shop.timesheet.2026-10.e_shop.1",
                CELL_B,
                expected_v=0,
                idem="net-or-off",
            )
            self.assertTrue(st.startswith("503"), (st, data))
        shop = next(w for w in self.store["payload"]["workspaces"] if w["code"] == "shop")
        self.assertEqual(shop["timesheet"]["2026-10"]["e_shop"]["1"], CELL_A)


class WtTimesheetOpPilotUiWiringTests(unittest.TestCase):
    """Статическая проверка: пилот в UI выключен по умолчанию и подключён к ячейке."""

    @classmethod
    def setUpClass(cls):
        cls.src = APP_HTML.read_text(encoding="utf-8")

    def test_ops_enablement_rules(self):
        """Локально — OPS on; удалённо — только после health.ops; SoftSync всегда."""
        self.assertIn('localStorage.getItem("wtOps")', self.src)
        self.assertIn("function wtOpsIsLocalHost(", self.src)
        self.assertIn("function wtOpsApplyUrlFlag(", self.src)
        self.assertIn("function wtOpsProbeServerCapability(", self.src)
        self.assertIn("wtOpsServerCapable === true", self.src)
        self.assertIn('h === "127.0.0.1" || h === "localhost"', self.src)
        self.assertIn('raw !== "1" && raw !== "0"', self.src)
        self.assertIn("OPS: ВКЛ", self.src)
        self.assertIn("OPS: ВЫКЛ", self.src)
        self.assertIn('/api/health', self.src)

    def test_cell_editors_use_saveTimesheetDayChange(self):
        for name in ("setStatus", "setHours", "setLateMinutes", "setEarlyArrival"):
            self.assertIn("function " + name + "(", self.src)
        self.assertIn("function finishTimesheetCellSave(", self.src)
        # После правки ячейки — finishTimesheetCellSave → op, не голый saveApp.
        for fn in ("setStatus", "setHours", "setLateMinutes", "setEarlyArrival"):
            start = self.src.find("function " + fn + "(")
            self.assertGreater(start, 0, fn)
            nxt = self.src.find("\nfunction ", start + 10)
            block = self.src[start:nxt]
            self.assertIn("finishTimesheetCellSave(", block, fn)
            self.assertNotIn("saveApp();", block, fn)
        # Сначала save, потом refreshAll — не наоборот.
        fin = self.src.find("function finishTimesheetCellSave(")
        fin_block = self.src[fin : self.src.find("\nfunction ", fin + 10)]
        self.assertLess(fin_block.find("saveTimesheetDayChange"), fin_block.find("refreshAll"))

    def test_op_helpers_present(self):
        self.assertIn("function saveTimesheetCellViaOp(", self.src)
        self.assertIn("function saveTimesheetDayChange(", self.src)
        self.assertIn("function wtTimesheetDayPath(", self.src)
        self.assertIn("function timesheetActiveDeptCode(", self.src)
        self.assertIn("function wtOpsStripTimesheetFromPutPayload(", self.src)
        self.assertIn("wtSaveRecordOp(path, value", self.src)
        self.assertIn("forceEnable: true", self.src)
        # При OPS: ВКЛ тихий fallback на PUT запрещён.
        m = re.search(
            r"function saveTimesheetDayChange\([\s\S]*?\nfunction ",
            self.src,
        )
        self.assertIsNotNone(m)
        block = m.group(0)
        self.assertIn("wtOpsUiEnabled(true)", block)
        self.assertIn('via: "put"', block)
        self.assertIn("saveTimesheetCellViaOp", block)
        self.assertIn("wtOpsBlockIncidentalPut = true", block)
        # Ветка OPS on не должна звать saveApp.
        ops_on = block.split("saveTimesheetCellViaOp")[-1]
        self.assertNotIn("saveApp()", ops_on)


if __name__ == "__main__":
    unittest.main()
