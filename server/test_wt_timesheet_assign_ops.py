# -*- coding: utf-8 -*-
"""
Узкое сохранение состава табеля (employeeIds) через OPS.
FakeConn + Node harness, без Timeweb / live БД.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from wsgiref.simple_server import WSGIRequestHandler, make_server

import wt_api
from test_fr_close_period_atomic import NODE
from test_wt_api import ADMIN, FakeConn, SECRET, WAREHOUSE

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
APP_HTML = (ROOT / "app.html").read_text(encoding="utf-8")
HARNESS = str(HERE / "wt_timesheet_assign_harness.js")


class Quiet(WSGIRequestHandler):
    def log_message(self, *args):
        pass


@unittest.skipUnless(NODE, "node не найден (WT_NODE_BIN)")
class WtTimesheetAssignOpsTests(unittest.TestCase):
    def setUp(self):
        self._env_backup = dict(os.environ)
        os.environ["DATABASE_URL"] = "postgresql://worktime:" + SECRET + "@127.0.0.1:5432/worktime"
        os.environ["WT_PIN_PEPPER"] = "unit-pepper"
        os.environ["WT_SCRYPT_N"] = "16"
        os.environ["WT_OPS_ENABLED"] = "1"
        os.environ.pop("WT_API_TOKEN", None)
        os.environ.pop("WT_COOKIE_SECURE", None)
        wt_api.LOGIN_FAILS.clear()
        payload = {
            "workspaces": [
                {
                    "id": "ws1",
                    "code": "shop",
                    "name": "Магазин",
                    "employees": [{"id": "e_shop", "name": "Касса", "jobStatus": "working"}],
                    "employeeIds": ["e_shop"],
                    "timesheet": {},
                },
                {
                    "id": "ws2",
                    "code": "warehouse",
                    "name": "Склад",
                    "employees": [
                        {"id": "e_wh", "name": "Кладовщик", "jobStatus": "working"},
                        {"id": "e_tat", "name": "Татьяна касса", "jobStatus": "working"},
                    ],
                    "employeeIds": ["e_wh"],
                    "timesheet": {},
                },
            ],
            "users": [
                json.loads(json.dumps(ADMIN)),
                json.loads(json.dumps(WAREHOUSE)),
            ],
            "shopMetrics": {},
        }
        self.store = {
            "payload": payload,
            "version": 4,
            "updated_at": datetime.now(timezone.utc),
            "updated_by": "",
            "fail": False,
            "sessions": {},
            "lock": threading.Lock(),
        }
        self._patch = patch.object(wt_api, "connect", side_effect=lambda: FakeConn(self.store))
        self._patch.start()
        self.httpd = make_server("127.0.0.1", 0, wt_api.application, handler_class=Quiet)
        self.base = "http://127.0.0.1:%d" % self.httpd.server_address[1]
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self._patch.stop()
        os.environ.clear()
        os.environ.update(self._env_backup)
        wt_api.LOGIN_FAILS.clear()

    def run_scenario(self, name, confirms=()):
        env = dict(os.environ)
        env["WT_TEST_BASE_URL"] = self.base
        env["WT_TEST_SCENARIO"] = name
        env["WT_TEST_CONFIRMS"] = json.dumps(list(confirms))
        proc = subprocess.run(
            [NODE, HARNESS],
            cwd=str(HERE),
            env=env,
            capture_output=True,
            timeout=120,
        )
        text = proc.stdout.decode("utf-8", errors="replace")
        for line in text.splitlines():
            if line.startswith("RESULT_JSON "):
                return json.loads(line[len("RESULT_JSON ") :])
        self.fail("harness: %s\n%s" % (text, proc.stderr.decode("utf-8", errors="replace")))

    def ws_ids(self, code="warehouse"):
        for w in self.store["payload"].get("workspaces") or []:
            if w.get("code") == code:
                return list(w.get("employeeIds") or [])
        return None

    def test_markers_narrow_save(self):
        self.assertIn("function wtSaveWorkspaceEmployeeIds(", APP_HTML)
        self.assertIn("function wtWorkspaceEmployeeIdsPath(", APP_HTML)
        # toggleAssign больше не вызывает голый saveApp() для состава табеля
        m = re.search(r"function toggleAssign\([\s\S]*?\n\}", APP_HTML)
        self.assertIsNotNone(m)
        body = m.group(0)
        self.assertIn("wtSaveWorkspaceEmployeeIds", body)
        self.assertNotRegex(body, r"(?m)^\s*saveApp\(\);\s*$")
        m2 = re.search(r"function setTimesheetEmployee\([\s\S]*?\n\}", APP_HTML)
        self.assertIsNotNone(m2)
        body2 = m2.group(0)
        self.assertIn("wtSaveWorkspaceEmployeeIds", body2)
        self.assertNotRegex(body2, r"(?m)^\s*saveApp\(\);\s*$")

    def test_add_survives_f5(self):
        out = self.run_scenario("add_f5")
        self.assertTrue(out["save"], out)
        self.assertEqual(out["afterF5"], ["e_wh", "e_tat"], out)
        self.assertEqual(self.ws_ids("warehouse"), ["e_wh", "e_tat"])
        # Другое подразделение не трогаем
        self.assertEqual(out.get("shopAfter") or self.ws_ids("shop"), ["e_shop"])
        self.assertEqual(out["log"]["puts"], 0)
        self.assertEqual(len(out["log"]["batches"]), 1)
        self.assertEqual(out["log"]["batches"][0]["paths"], ["workspaces.warehouse.employeeIds"])

    def test_remove_survives_f5(self):
        out = self.run_scenario("remove_f5")
        self.assertTrue(out["save"], out)
        self.assertEqual(out["afterF5"], ["e_wh"], out)
        self.assertEqual(self.ws_ids("warehouse"), ["e_wh"])

    def test_noise_409_sends_only_employee_ids(self):
        out = self.run_scenario("noise_409")
        self.assertGreater(out["collectCount"], 200, out)
        self.assertTrue(out["save"], out)
        self.assertEqual(out["afterF5"], ["e_wh", "e_tat"], out)
        self.assertEqual(out["batchSizes"], [1], out)
        self.assertEqual(out["batchPaths"], [["workspaces.warehouse.employeeIds"]], out)
        # Нет отказа «Слишком много изменений…»
        self.assertFalse(
            any("Слишком много изменений" in t.get("msg", "") for t in out.get("toasts") or []),
            out.get("toasts"),
        )

    def test_forbidden_reverts_local(self):
        out = self.run_scenario("forbidden_revert")
        self.assertFalse(out["save"], out)
        self.assertEqual(out["local"], out["before"], out)
        self.assertEqual(out["afterF5"], ["e_wh"], out)
        self.assertEqual(self.ws_ids("warehouse"), ["e_wh"])

    def test_stale_ops_does_not_wipe_newer_list(self):
        out = self.run_scenario("two_users_conflict")
        self.assertTrue(out["saveA"], out)
        # Устаревший expected_v → conflict, список с e_tat сохраняется
        self.assertIn(out["bStatus"], (400, 409), out)
        results = (out.get("bBody") or {}).get("results") or []
        if results:
            self.assertTrue(
                any((r.get("status") == "conflict" or r.get("error") == "conflict") for r in results),
                out,
            )
        else:
            self.assertEqual((out.get("bBody") or {}).get("error"), "conflict", out)
        self.assertEqual(out["db"], ["e_wh", "e_tat"], out)
        self.assertEqual(self.ws_ids("warehouse"), ["e_wh", "e_tat"])


if __name__ == "__main__":
    unittest.main()
