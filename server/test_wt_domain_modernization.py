# -*- coding: utf-8 -*-
"""
Единая архитектура DomainWriter / batch-op / soft-sync (модернизация).
FakeConn + live PostgreSQL worktime_test + wiring app.html.
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


class WtDomainWiringTests(unittest.TestCase):
    def test_app_html_has_domain_writer(self):
        text = APP_HTML.read_text(encoding="utf-8")
        self.assertIn("function wtDomainWriterSave", text)
        self.assertIn("function wtCollectChangedLeaves", text)
        self.assertIn("/api/state/op/batch", text)
        self.assertIn("WT_DOMAIN_TRACKED_TOPS", text)
        # OPS по умолчанию на localhost
        self.assertIn("wtOpsIsLocalHost()", text)
        # Dual-write атомарный batch
        self.assertIn("atomic: true", text)
        self.assertIn("wtSoftOnOnline", text)


class WtBatchOpApiTests(unittest.TestCase):
    def setUp(self):
        self._env_backup = dict(os.environ)
        os.environ["DATABASE_URL"] = "postgresql://worktime:" + SECRET + "@127.0.0.1:5432/worktime"
        os.environ["WT_PIN_PEPPER"] = "unit-pepper"
        os.environ["WT_SCRYPT_N"] = "16"
        os.environ["WT_OPS_ENABLED"] = "1"
        os.environ.pop("WT_API_TOKEN", None)
        wt_api.LOGIN_FAILS.clear()
        payload = json.loads(json.dumps(SAMPLE_APP))
        # сотрудники с id для path-by-id
        for ws in payload.get("workspaces") or []:
            if isinstance(ws, dict) and ws.get("code") == "shop":
                emps = ws.get("employees")
                if isinstance(emps, list) and emps:
                    if not emps[0].get("id"):
                        emps[0]["id"] = "e_shop"
                else:
                    ws["employees"] = [{"id": "e_shop", "name": "Тест"}]
                ts = ws.setdefault("timesheet", {})
                month = ts.setdefault("2026-10", {})
                month.setdefault("e_shop", {}).setdefault("3", {"status": "work", "hours": "8"})
        payload["shopMetrics"] = {
            "facts": {"2026-10": {"e_shop": {"5": {"metric_supplier_orders": 1}}}},
            "pickers": {"2026-10": {"e_shop": {"5": {"recount": 1}}}},
        }
        payload["salary"] = {"manual": {"2026-10": {"e_shop": {"bonus": 10}}}}
        payload["financeRec"] = {
            "operations": {"op1": {"amount": 100, "comment": "a"}},
            "sources": [],
        }
        self.store = {
            "payload": payload,
            "version": 10,
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

    def batch(self, cookie, ops, atomic=False, idem=None):
        body = {"ops": ops, "atomic": atomic}
        if idem:
            body["idempotency_key"] = idem
        return call("POST", "/api/state/op/batch", body, cookie=cookie)

    def test_batch_independent_paths(self):
        cookie = self.login()
        st, data, *_ = self.batch(
            cookie,
            [
                {
                    "path": "shopMetrics.facts.2026-10.e_shop.5.metric_supplier_orders",
                    "expected_v": 0,
                    "value": 99,
                },
                {
                    "path": "salary.manual.2026-10.e_shop.bonus",
                    "expected_v": 0,
                    "value": 50,
                },
            ],
            atomic=False,
            idem="batch-indep-1",
        )
        self.assertTrue(st.startswith("200"), (st, data))
        self.assertTrue(data.get("ok"))
        self.assertEqual(len(data.get("results") or []), 2)
        self.assertEqual(
            self.store["payload"]["shopMetrics"]["facts"]["2026-10"]["e_shop"]["5"][
                "metric_supplier_orders"
            ],
            99,
        )
        self.assertEqual(self.store["payload"]["salary"]["manual"]["2026-10"]["e_shop"]["bonus"], 50)

    def test_batch_atomic_dual_metric(self):
        cookie = self.login()
        st, data, *_ = self.batch(
            cookie,
            [
                {
                    "path": "shopMetrics.facts.2026-10.e_shop.5.metric_supplier_orders",
                    "expected_v": 0,
                    "value": 7,
                },
                {
                    "path": "shopMetrics.pickers.2026-10.e_shop.5.recount",
                    "expected_v": 0,
                    "value": 7,
                },
            ],
            atomic=True,
            idem="batch-atomic-1",
        )
        self.assertTrue(st.startswith("200"), (st, data))
        self.assertEqual(
            self.store["payload"]["shopMetrics"]["facts"]["2026-10"]["e_shop"]["5"][
                "metric_supplier_orders"
            ],
            7,
        )
        self.assertEqual(
            self.store["payload"]["shopMetrics"]["pickers"]["2026-10"]["e_shop"]["5"]["recount"],
            7,
        )

    def test_batch_atomic_conflict_rolls_back(self):
        cookie = self.login()
        # Поднимаем record_v на facts
        call(
            "POST",
            "/api/state/op",
            {
                "op": {
                    "path": "shopMetrics.facts.2026-10.e_shop.5.metric_supplier_orders",
                    "expected_v": 0,
                    "value": 2,
                }
            },
            cookie=cookie,
        )
        st, data, *_ = self.batch(
            cookie,
            [
                {
                    "path": "shopMetrics.facts.2026-10.e_shop.5.metric_supplier_orders",
                    "expected_v": 0,  # устарело
                    "value": 100,
                },
                {
                    "path": "shopMetrics.pickers.2026-10.e_shop.5.recount",
                    "expected_v": 0,
                    "value": 100,
                },
            ],
            atomic=True,
        )
        self.assertTrue(st.startswith("409"), (st, data))
        # pickers не должен измениться атомарным откатом
        self.assertEqual(
            self.store["payload"]["shopMetrics"]["pickers"]["2026-10"]["e_shop"]["5"]["recount"],
            1,
        )

    def test_employee_path_by_id(self):
        cookie = self.login()
        path = "workspaces.shop.employees.e_shop.name"
        st, data, *_ = call(
            "POST",
            "/api/state/op",
            {"op": {"path": path, "expected_v": 0, "value": "Новое Имя"}},
            cookie=cookie,
        )
        self.assertTrue(st.startswith("200"), (st, data))
        emps = None
        for ws in self.store["payload"]["workspaces"]:
            if ws.get("code") == "shop":
                emps = ws.get("employees")
        self.assertTrue(emps)
        hit = next(e for e in emps if str(e.get("id")) == "e_shop")
        self.assertEqual(hit.get("name"), "Новое Имя")

    def test_two_users_different_domains(self):
        admin_c = self.login("user_admin", "5555")
        staff_c = self.login("usr_001", "1111")
        results = {}

        def admin_job():
            st, d, *_ = self.batch(
                admin_c,
                [
                    {
                        "path": "financeRec.operations.op1.amount",
                        "expected_v": 0,
                        "value": 555,
                    }
                ],
                idem="adm-fin",
            )
            results["admin"] = (st, d)

        def staff_job():
            st, d, *_ = call(
                "POST",
                "/api/state/op",
                {
                    "idempotency_key": "staff-ts",
                    "op": {
                        "path": "workspaces.shop.timesheet.2026-10.e_shop.3",
                        "expected_v": 0,
                        "value": {"status": "work", "hours": "9"},
                    },
                },
                cookie=staff_c,
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
        self.assertEqual(self.store["payload"]["financeRec"]["operations"]["op1"]["amount"], 555)
        self.assertEqual(
            self.store["payload"]["workspaces"][0]["timesheet"]["2026-10"]["e_shop"]["3"]["hours"],
            "9",
        )

    def test_same_path_conflict(self):
        a = self.login("user_admin", "5555")
        b = self.login("usr_001", "1111")
        path = "workspaces.shop.timesheet.2026-10.e_shop.3"
        st1, *_ = call(
            "POST",
            "/api/state/op",
            {"op": {"path": path, "expected_v": 0, "value": {"status": "work", "hours": "1"}}},
            cookie=a,
        )
        self.assertTrue(st1.startswith("200"), st1)
        st2, data2, *_ = call(
            "POST",
            "/api/state/op",
            {"op": {"path": path, "expected_v": 0, "value": {"status": "work", "hours": "2"}}},
            cookie=b,
        )
        self.assertTrue(st2.startswith("409"), (st2, data2))
        self.assertEqual(data2.get("error"), "conflict")


@unittest.skipUnless(
    _RAW_URL and "worktime_test" in _RAW_URL and ("127.0.0.1" in _RAW_URL or "localhost" in _RAW_URL),
    "need local DATABASE_URL/WT_TEST_DATABASE_URL for worktime_test",
)
class WtDomainLiveLoadTests(unittest.TestCase):
    """Нагрузка и dual-session на живой worktime_test (через HTTP :8780 или in-process)."""

    @classmethod
    def setUpClass(cls):
        from wt_ops_pg_safety import assert_safe_test_database_url

        cls.url = assert_safe_test_database_url(_RAW_URL)
        os.environ["DATABASE_URL"] = cls.url
        os.environ.setdefault("WT_PIN_PEPPER", "unit-pepper")
        os.environ["WT_OPS_ENABLED"] = "1"
        os.environ.pop("WT_API_TOKEN", None)

    def _base(self):
        return os.environ.get("WT_LIVE_BASE", "http://127.0.0.1:8780").rstrip("/")

    def _login(self, user_id, pin):
        import urllib.request

        req = urllib.request.Request(
            self._base() + "/api/login",
            data=json.dumps({"user_id": user_id, "pin": pin}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            cookie = resp.headers.get("Set-Cookie", "").split(";")[0]
        self.assertTrue(body.get("ok") or body.get("user") or cookie, body)
        return cookie

    def _op(self, cookie, path, value, expected_v=0):
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
        except Exception as exc:
            if hasattr(exc, "read"):
                raw = exc.read().decode("utf-8", errors="replace")
                try:
                    return getattr(exc, "code", 0), json.loads(raw)
                except Exception:
                    return getattr(exc, "code", 0), {"raw": raw}
            raise

    def _batch(self, cookie, ops, atomic=False):
        import urllib.request

        req = urllib.request.Request(
            self._base() + "/api/state/op/batch",
            data=json.dumps({"ops": ops, "atomic": atomic}).encode("utf-8"),
            headers={"Content-Type": "application/json", "Cookie": cookie},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            if hasattr(exc, "read"):
                raw = exc.read().decode("utf-8", errors="replace")
                try:
                    return getattr(exc, "code", 0), json.loads(raw)
                except Exception:
                    return getattr(exc, "code", 0), {"raw": raw}
            raise

    def _get_state(self, cookie):
        import urllib.request

        req = urllib.request.Request(
            self._base() + "/api/state",
            headers={"Cookie": cookie},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def test_live_batch_and_version_bump(self):
        cookie = self._login("user_admin", "5555")
        state = self._get_state(cookie)
        v0 = int(state.get("version") or 0)
        month = "2026-10"
        path = f"shopMetrics.store.{month}.26.visitors"
        # узнать текущий record_v
        rec = (state.get("payload") or {}).get("_wt_records") or {}
        expected = int((rec.get(path) or {}).get("v") or 0)
        st, data = self._batch(
            cookie,
            [{"path": path, "expected_v": expected, "value": 12345}],
            atomic=False,
        )
        if st == 409:
            expected = int(data.get("server_v") or expected + 1)
            st, data = self._batch(
                cookie,
                [{"path": path, "expected_v": expected, "value": 12345}],
            )
        self.assertEqual(st, 200, data)
        self.assertTrue(data.get("ok"))
        self.assertGreaterEqual(int(data.get("version") or 0), v0 + 1)

    def test_live_parallel_clients_2_5_10_20_50(self):
        """Параллельные version-poll + редкие batch writes (нагрузка до 50)."""
        cookie = self._login("user_admin", "5555")
        import urllib.request

        def poll_once():
            req = urllib.request.Request(
                self._base() + "/api/state/version",
                headers={"Cookie": cookie},
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode("utf-8"))

        for n in (2, 5, 10, 20, 50):
            errors = []
            versions = []
            lock = threading.Lock()

            def worker(i):
                try:
                    for round_i in range(3):
                        meta = None
                        for attempt in range(10):
                            try:
                                meta = poll_once()
                                break
                            except Exception:
                                time.sleep(0.08 * (attempt + 1))
                        if meta is None:
                            with lock:
                                errors.append((i, "poll_failed"))
                            return
                        with lock:
                            versions.append(meta.get("version"))
                        time.sleep(0.04 + (i % 7) * 0.003)
                    # Пишет только один клиент на волну — иначе шторм FOR UPDATE на одном JSON.
                    if i == 0:
                        path = f"shopMetrics.store.2026-10.{10 + (n % 20)}.visitors"
                        st, data = 0, {}
                        for attempt in range(6):
                            try:
                                st, data = self._op(cookie, path, 1000 + n, expected_v=0)
                                if st == 409:
                                    st, data = self._op(
                                        cookie,
                                        path,
                                        1000 + n,
                                        expected_v=int(data.get("server_v") or 1),
                                    )
                                break
                            except Exception as exc:
                                st, data = 0, {"error": str(exc)}
                                time.sleep(0.08 * (attempt + 1))
                        if st not in (200, 409):
                            with lock:
                                errors.append((i, st, data))
                except Exception as exc:
                    with lock:
                        errors.append((i, str(exc)))

            threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
            t0 = time.time()
            for t in threads:
                t.start()
                time.sleep(0.005)  # чуть растянуть старт, не бить backlog
            for t in threads:
                t.join(timeout=120)
            elapsed = time.time() - t0
            self.assertLess(elapsed, 120, f"n={n} too slow: {elapsed}")
            # Допускаем единичные транзиентные poll_failed на пике 50 (сокеты Windows),
            # но не более 10% клиентов и при этом должны быть успешные version-ответы.
            hard = [e for e in errors if e[1] != "poll_failed"]
            poll_fail = [e for e in errors if e[1] == "poll_failed"]
            self.assertEqual(hard, [], f"n={n} hard errors={hard[:3]}")
            self.assertLessEqual(len(poll_fail), max(1, n // 10), f"n={n} poll_failed={poll_fail}")
            self.assertGreaterEqual(len(versions), n * 2, f"n={n} versions={len(versions)}")
            time.sleep(0.3)


if __name__ == "__main__":
    unittest.main()
