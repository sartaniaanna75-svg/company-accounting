# -*- coding: utf-8 -*-
"""
Финальная матрица синхронизации: типы данных, dual-session, конфликты,
запреты прав, нагрузка конкурентных записей 5/10/20/50.
Только worktime_test / 127.0.0.1.
"""
from __future__ import annotations

import json
import os
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import wt_api
from test_wt_api import SAMPLE_APP, FakeConn, SECRET, call, cookie_from

APP_HTML = Path(__file__).resolve().parent.parent / "app.html"
_RAW_URL = (os.environ.get("WT_TEST_DATABASE_URL") or os.environ.get("DATABASE_URL") or "").strip()


class WtFinalWiringTests(unittest.TestCase):
    def test_domain_writer_and_batch_still_wired(self):
        text = APP_HTML.read_text(encoding="utf-8")
        self.assertIn("function wtDomainWriterSave", text)
        self.assertIn("/api/state/op/batch", text)
        self.assertIn("wtSoftOnOnline", text)
        self.assertIn("wtSoftMarkDirty", text)
        # Ревизия в навигации только при revision:view
        self.assertIn('userHasPerm("revision", "view")', text)
        self.assertIn('list.push("revision")', text)


class WtFinalMatrixApiTests(unittest.TestCase):
    def setUp(self):
        self._env_backup = dict(os.environ)
        os.environ["DATABASE_URL"] = "postgresql://worktime:" + SECRET + "@127.0.0.1:5432/worktime"
        os.environ["WT_PIN_PEPPER"] = "unit-pepper"
        os.environ["WT_SCRYPT_N"] = "16"
        os.environ["WT_OPS_ENABLED"] = "1"
        os.environ.pop("WT_API_TOKEN", None)
        wt_api.LOGIN_FAILS.clear()
        payload = json.loads(json.dumps(SAMPLE_APP))
        for ws in payload.get("workspaces") or []:
            if isinstance(ws, dict) and ws.get("code") == "shop":
                ws["employees"] = [
                    {"id": "e_shop", "name": "Сотрудник А"},
                    {"id": "e_shop2", "name": "Сотрудник Б"},
                ]
                ws.setdefault("timesheet", {}).setdefault("2026-10", {})
                ws["timesheet"]["2026-10"]["e_shop"] = {
                    "3": {"status": "work", "hours": "8"},
                    "4": {"status": "work", "hours": "8"},
                }
                ws["timesheet"]["2026-10"]["e_shop2"] = {
                    "3": {"status": "work", "hours": "8"},
                }
        payload["shopMetrics"] = {
            "facts": {
                "2026-10": {
                    "e_shop": {"5": {"metric_supplier_orders": 1, "metric_revision": 0}},
                    "e_shop2": {"5": {"metric_supplier_orders": 2}},
                }
            },
            "pickers": {
                "2026-10": {
                    "e_shop": {
                        "5": {"recount": 1, "displayMark": "done"},
                        "6": {"recount": 0},
                    },
                    "e_shop2": {"5": {"recount": 3}},
                }
            },
            "cashiers": {"2026-10": {"e_shop": {"5": {"sales": 10}}}},
            "managers": {"2026-10": {"e_shop": {"5": {"tasks": 1}}}},
            "store": {"2026-10": {"5": {"visitors": 10}}},
        }
        payload["shopRevision"] = {
            "groups": [{"id": "g1", "name": "Группа"}],
            "folders": [],
            "tasks": [{"id": "t1", "name": "Задача", "done": False}],
            "ui": {"collapsedGroups": []},
        }
        payload["shopSalesPlan"] = {"months": {"2026-10": {"days": {"5": {"plan": 100}}}}}
        payload["salary"] = {"manual": {"2026-10": {"e_shop": {"bonus": 10, "note": "ok"}}}}
        payload["financeRec"] = {
            "operations": {"op1": {"amount": 100, "comment": "a", "date": "2026-10-05"}},
            "sources": [{"id": "s1", "name": "Касса"}],
            "openings": {},
        }
        payload["vacationPlans"] = {"2026": {"e_shop": {"days": [1, 2]}}}
        payload["navLayouts"] = {"shop": {"order": ["timesheet", "revision"]}}
        payload["customerReturns"] = {"items": [{"id": "cr1", "status": "open"}], "vehicles": [], "adjustments": []}
        payload["orgStructure"] = {"nodes": [{"id": "n1", "title": "Узел"}]}
        # Staff usr_001: revision view only (not edit) if SAMPLE already sets — enforce
        for u in payload.get("users") or []:
            if u.get("user_id") == "usr_001":
                perms = dict(u.get("permissions") or {})
                perms["revision"] = "view"
                perms.setdefault("timesheet", "edit")
                perms.setdefault("metricsInput", "edit")
                u["permissions"] = perms
        self.store = {
            "payload": payload,
            "version": 20,
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
        st, data, _, cookies = call("POST", "/api/login", {"user_id": user_id, "pin": pin})
        self.assertTrue(st.startswith("200"), st)
        return cookie_from(cookies)

    def op(self, cookie, path, value, expected_v=0, idem=None):
        body = {"op": {"path": path, "expected_v": expected_v, "value": value}}
        if idem:
            body["idempotency_key"] = idem
        return call("POST", "/api/state/op", body, cookie=cookie)

    def batch(self, cookie, ops, atomic=False):
        return call(
            "POST",
            "/api/state/op/batch",
            {"ops": ops, "atomic": atomic},
            cookie=cookie,
        )

    def test_number_text_status_checkbox_date_list(self):
        c = self.login()
        cases = [
            ("shopMetrics.pickers.2026-10.e_shop.5.recount", 42),  # number
            ("salary.manual.2026-10.e_shop.note", "текст"),  # text
            ("workspaces.shop.timesheet.2026-10.e_shop.3", {"status": "sick", "hours": ""}),  # status
            ("shopMetrics.pickers.2026-10.e_shop.5.displayMark", "fail"),  # checkbox/mark
            ("financeRec.operations.op1.date", "2026-10-26"),  # date-like
            ("shopRevision.tasks", [{"id": "t1", "name": "Задача", "done": True}]),  # list/array
            ("vacationPlans.2026.e_shop.days", [1, 2, 3]),  # array
            ("orgStructure.nodes.0.title", "Узел-2"),  # nested — may be whole node leaf
        ]
        # orgStructure.nodes is list — leaf may be whole list; use nodes replace carefully
        cases[-1] = (
            "orgStructure.nodes",
            [{"id": "n1", "title": "Узел-2"}],
        )
        for path, value in cases:
            st, data, *_ = self.op(c, path, value, expected_v=0, idem="t-" + path.replace(".", "_")[:80])
            if st.startswith("409"):
                st, data, *_ = self.op(
                    c, path, value, expected_v=int(data.get("server_v") or 1), idem="t2-" + path[:60]
                )
            self.assertTrue(st.startswith("200"), (path, st, data))
            self.assertEqual(wt_api.get_path_value(self.store["payload"], path), value)

    def test_dictionary_and_related_create_delete(self):
        c = self.login()
        # create-like: set new employee by id path
        st, data, *_ = self.op(
            c,
            "workspaces.shop.employees.e_new",
            {"id": "e_new", "name": "Новый"},
            0,
            idem="emp-create",
        )
        self.assertTrue(st.startswith("200"), (st, data))
        emps = None
        for ws in self.store["payload"]["workspaces"]:
            if ws.get("code") == "shop":
                emps = ws["employees"]
        self.assertTrue(any(e.get("id") == "e_new" for e in emps))
        # delete employee
        st, data, *_ = self.op(c, "workspaces.shop.employees.e_new", None, int(data.get("record_v") or 1))
        self.assertTrue(st.startswith("200"), (st, data))
        for ws in self.store["payload"]["workspaces"]:
            if ws.get("code") == "shop":
                self.assertFalse(any(e.get("id") == "e_new" for e in ws["employees"]))

    def test_dual_session_different_cells_same_table(self):
        a = self.login("user_admin", "5555")
        b = self.login("usr_001", "1111")
        results = {}

        def job_a():
            st, d, *_ = self.op(
                a,
                "workspaces.shop.timesheet.2026-10.e_shop.3",
                {"status": "work", "hours": "7"},
                0,
                idem="dual-a",
            )
            results["a"] = (st, d)

        def job_b():
            st, d, *_ = self.op(
                b,
                "workspaces.shop.timesheet.2026-10.e_shop.4",
                {"status": "work", "hours": "6"},
                0,
                idem="dual-b",
            )
            results["b"] = (st, d)

        t1 = threading.Thread(target=job_a)
        t2 = threading.Thread(target=job_b)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        self.assertTrue(results["a"][0].startswith("200"), results["a"])
        self.assertTrue(results["b"][0].startswith("200"), results["b"])
        ts = None
        for ws in self.store["payload"]["workspaces"]:
            if ws.get("code") == "shop":
                ts = ws["timesheet"]["2026-10"]["e_shop"]
        self.assertEqual(ts["3"]["hours"], "7")
        self.assertEqual(ts["4"]["hours"], "6")

    def test_dual_session_different_metrics_same_employee(self):
        a = self.login("user_admin", "5555")
        b = self.login("usr_001", "1111")
        ra = {}
        rb = {}

        def ja():
            st, d, *_ = self.op(
                a,
                "shopMetrics.facts.2026-10.e_shop.5.metric_supplier_orders",
                11,
                0,
                idem="m-a",
            )
            ra["x"] = (st, d)

        def jb():
            st, d, *_ = self.op(
                b,
                "shopMetrics.pickers.2026-10.e_shop.5.recount",
                22,
                0,
                idem="m-b",
            )
            rb["x"] = (st, d)

        t1 = threading.Thread(target=ja)
        t2 = threading.Thread(target=jb)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        self.assertTrue(ra["x"][0].startswith("200"), ra)
        self.assertTrue(rb["x"][0].startswith("200"), rb)
        self.assertEqual(
            self.store["payload"]["shopMetrics"]["facts"]["2026-10"]["e_shop"]["5"][
                "metric_supplier_orders"
            ],
            11,
        )
        self.assertEqual(
            self.store["payload"]["shopMetrics"]["pickers"]["2026-10"]["e_shop"]["5"]["recount"],
            22,
        )

    def test_same_record_conflict_preserved(self):
        a = self.login("user_admin", "5555")
        b = self.login("usr_001", "1111")
        path = "shopMetrics.pickers.2026-10.e_shop.5.recount"
        st1, *_ = self.op(a, path, 5, 0)
        self.assertTrue(st1.startswith("200"), st1)
        st2, d2, *_ = self.op(b, path, 9, 0)
        self.assertTrue(st2.startswith("409"), (st2, d2))
        self.assertEqual(d2.get("error"), "conflict")
        self.assertEqual(
            self.store["payload"]["shopMetrics"]["pickers"]["2026-10"]["e_shop"]["5"]["recount"],
            5,
        )

    def test_staff_cannot_edit_revision_via_api(self):
        c = self.login("usr_001", "1111")
        st, data, *_ = self.op(
            c,
            "shopRevision.tasks",
            [{"id": "t1", "name": "hack", "done": True}],
            0,
        )
        self.assertTrue(st.startswith("403") or data.get("error") == "forbidden", (st, data))
        # original intact
        self.assertEqual(self.store["payload"]["shopRevision"]["tasks"][0]["name"], "Задача")

    def test_staff_state_hides_revision_without_view(self):
        # temporarily strip revision view
        for u in self.store["payload"]["users"]:
            if u.get("user_id") == "usr_001":
                u["permissions"]["revision"] = "none"
        c = self.login("usr_001", "1111")
        st, data, *_ = call("GET", "/api/state", cookie=c)
        self.assertTrue(st.startswith("200"), st)
        payload = data.get("payload") or data
        self.assertNotIn("shopRevision", payload)

    def test_atomic_batch_checklist_and_number(self):
        c = self.login()
        st, data, *_ = self.batch(
            c,
            [
                {
                    "path": "shopMetrics.pickers.2026-10.e_shop.5.recount",
                    "expected_v": 0,
                    "value": 8,
                },
                {
                    "path": "shopMetrics.pickers.2026-10.e_shop.5.displayMark",
                    "expected_v": 0,
                    "value": "done",
                },
            ],
            atomic=True,
        )
        self.assertTrue(st.startswith("200"), (st, data))
        row = self.store["payload"]["shopMetrics"]["pickers"]["2026-10"]["e_shop"]["5"]
        self.assertEqual(row["recount"], 8)
        self.assertEqual(row["displayMark"], "done")

    def test_finance_and_salary_independent(self):
        a = self.login("user_admin", "5555")
        st1, *_ = self.op(a, "financeRec.operations.op1.amount", 250, 0, idem="fin1")
        st2, *_ = self.op(a, "salary.manual.2026-10.e_shop.bonus", 33, 0, idem="sal1")
        self.assertTrue(st1.startswith("200") and st2.startswith("200"))
        self.assertEqual(self.store["payload"]["financeRec"]["operations"]["op1"]["amount"], 250)
        self.assertEqual(self.store["payload"]["salary"]["manual"]["2026-10"]["e_shop"]["bonus"], 33)

    def test_sales_plan_and_returns(self):
        c = self.login()
        st, data, *_ = self.op(
            c, "shopSalesPlan.months.2026-10.days.5.plan", 500, 0, idem="plan1"
        )
        self.assertTrue(st.startswith("200"), (st, data))
        st, data, *_ = self.op(
            c,
            "customerReturns.items",
            [{"id": "cr1", "status": "closed"}],
            0,
            idem="cr1",
        )
        self.assertTrue(st.startswith("200"), (st, data))


@unittest.skipUnless(
    _RAW_URL and "worktime_test" in _RAW_URL and ("127.0.0.1" in _RAW_URL or "localhost" in _RAW_URL),
    "need local worktime_test DATABASE_URL",
)
class WtFinalLiveConcurrentWrites(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from wt_ops_pg_safety import assert_safe_test_database_url

        cls.url = assert_safe_test_database_url(_RAW_URL)
        os.environ["DATABASE_URL"] = cls.url
        os.environ.setdefault("WT_PIN_PEPPER", "unit-pepper")
        os.environ["WT_OPS_ENABLED"] = "1"

    def _base(self):
        return os.environ.get("WT_LIVE_BASE", "http://127.0.0.1:8780").rstrip("/")

    def _login(self):
        import urllib.request

        req = urllib.request.Request(
            self._base() + "/api/login",
            data=json.dumps({"user_id": "user_admin", "pin": "5555"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            cookie = (resp.headers.get("Set-Cookie") or "").split(";")[0]
        return cookie

    def _op(self, cookie, path, value, expected_v=0):
        import urllib.error
        import urllib.request

        req = urllib.request.Request(
            self._base() + "/api/state/op",
            data=json.dumps(
                {"op": {"path": path, "expected_v": expected_v, "value": value}}
            ).encode("utf-8"),
            headers={"Content-Type": "application/json", "Cookie": cookie},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace")
            try:
                return e.code, json.loads(raw)
            except Exception:
                return e.code, {"raw": raw}

    def test_concurrent_writes_5_10_20_50_distinct_paths(self):
        cookie = self._login()
        for n in (5, 10, 20, 50):
            errors = []
            lock = threading.Lock()
            saved = []

            def worker(i):
                path = f"shopMetrics.store.2026-10.{(i % 28) + 1}.visitors"
                val = 5000 + n * 100 + i
                st, data = 0, {}
                for attempt in range(8):
                    try:
                        st, data = self._op(cookie, path, val, expected_v=0)
                        if st == 409:
                            st, data = self._op(
                                cookie, path, val, expected_v=int(data.get("server_v") or 1)
                            )
                        if st in (200, 409):
                            break
                    except Exception as exc:
                        st, data = 0, {"error": str(exc)}
                        time.sleep(0.05 * (attempt + 1))
                with lock:
                    if st == 200:
                        saved.append(path)
                    elif st != 409:
                        errors.append((i, st, data))

            threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
            t0 = time.time()
            for t in threads:
                t.start()
                time.sleep(0.003)
            for t in threads:
                t.join(timeout=180)
            elapsed = time.time() - t0
            self.assertEqual(errors, [], f"n={n} errors={errors[:2]}")
            self.assertGreaterEqual(len(saved), max(1, n // 2), f"n={n} saved={len(saved)}")
            self.assertLess(elapsed, 180, f"n={n} slow {elapsed}")
            time.sleep(0.2)


if __name__ == "__main__":
    unittest.main()
