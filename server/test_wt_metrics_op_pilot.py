# -*- coding: utf-8 -*-
"""Этап 16: пилот OPS для числовых leaf-метрик (FakeConn + wiring app.html + live)."""

from __future__ import annotations

import json
import os
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import wt_api
from test_wt_api import SAMPLE_APP, FakeConn, SECRET, call, cookie_from

APP_HTML = Path(__file__).resolve().parent.parent / "app.html"


class WtMetricsOpPilotApiTests(unittest.TestCase):
    def setUp(self):
        self._env_backup = dict(os.environ)
        os.environ["DATABASE_URL"] = "postgresql://worktime:" + SECRET + "@127.0.0.1:5432/worktime"
        os.environ["WT_PIN_PEPPER"] = "unit-pepper"
        os.environ["WT_SCRYPT_N"] = "16"
        os.environ["WT_OPS_ENABLED"] = "1"
        os.environ.pop("WT_API_TOKEN", None)
        wt_api.LOGIN_FAILS.clear()
        payload = json.loads(json.dumps(SAMPLE_APP))
        payload["shopMetrics"] = {
            "facts": {"2026-10": {"e_shop": {"5": {"metric_supplier_orders": 1}}}},
            "store": {"2026-10": {"5": {"visitors": 10, "source": "manual"}}},
            "channelValues": {"2026-10": {"self_checkout": {"5": {"sales": 100}}}},
            "pickers": {"2026-10": {"e_shop": {"5": {"display": 2}}}},
        }
        payload["deptMetrics"] = {
            "warehouse": {
                "facts": {"2026-10": {"e_wh": {"5": {"metric_supplier_orders": 3}}}},
            }
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

    def test_normalize_rejects_submetric_colon(self):
        bad = "shopMetrics.facts.2026-10.e_shop.5.metric_sales::online"
        self.assertIsNone(wt_api.normalize_op_path(bad))

    def test_shop_facts_leaf_op(self):
        cookie = self.login()
        path = "shopMetrics.facts.2026-10.e_shop.5.metric_supplier_orders"
        st, data, *_ = self.op(cookie, path, 42, expected_v=0)
        self.assertTrue(st.startswith("200"), (st, data))
        self.assertEqual(
            self.store["payload"]["shopMetrics"]["facts"]["2026-10"]["e_shop"]["5"]["metric_supplier_orders"],
            42,
        )

    def test_two_users_different_metric_leaves(self):
        admin_c = self.login("user_admin", "5555")
        staff_c = self.login("usr_001", "1111")
        results = {}

        def admin_job():
            st, d, *_ = self.op(
                admin_c,
                "shopMetrics.store.2026-10.5.visitors",
                77,
                expected_v=0,
                idem="m-admin-visitors",
            )
            results["admin"] = (st, d)

        def staff_job():
            st, d, *_ = self.op(
                staff_c,
                "shopMetrics.facts.2026-10.e_shop.5.metric_supplier_orders",
                9,
                expected_v=0,
                idem="m-staff-fact",
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
        self.assertEqual(self.store["payload"]["shopMetrics"]["store"]["2026-10"]["5"]["visitors"], 77)
        self.assertEqual(
            self.store["payload"]["shopMetrics"]["facts"]["2026-10"]["e_shop"]["5"]["metric_supplier_orders"],
            9,
        )

    def test_same_leaf_conflict(self):
        a = self.login("user_admin", "5555")
        b = self.login("usr_001", "1111")
        path = "shopMetrics.store.2026-10.5.visitors"
        st1, *_ = self.op(a, path, 11, expected_v=0, idem="m-c1")
        self.assertTrue(st1.startswith("200"), st1)
        st2, data2, *_ = self.op(b, path, 22, expected_v=0, idem="m-c2")
        self.assertTrue(st2.startswith("409"), (st2, data2))
        self.assertEqual(data2.get("error"), "conflict")
        self.assertEqual(self.store["payload"]["shopMetrics"]["store"]["2026-10"]["5"]["visitors"], 11)

    def test_dept_facts_leaf(self):
        cookie = self.login()
        path = "deptMetrics.warehouse.facts.2026-10.e_wh.5.metric_supplier_orders"
        st, data, *_ = self.op(cookie, path, 15, expected_v=0)
        self.assertTrue(st.startswith("200"), (st, data))
        self.assertEqual(
            self.store["payload"]["deptMetrics"]["warehouse"]["facts"]["2026-10"]["e_wh"]["5"][
                "metric_supplier_orders"
            ],
            15,
        )

    def test_ops_disabled_503(self):
        os.environ["WT_OPS_ENABLED"] = "0"
        cookie = self.login()
        st, data, *_ = self.op(cookie, "shopMetrics.store.2026-10.5.visitors", 1, expected_v=0)
        self.assertTrue(st.startswith("503"), (st, data))


class WtMetricsOpPilotUiWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = APP_HTML.read_text(encoding="utf-8")

    def test_helpers_present(self):
        for name in (
            "wtShopFactsLeafPath",
            "wtShopCategoryLeafPath",
            "wtShopStoreLeafPath",
            "wtShopChannelLeafPath",
            "wtDeptFactsLeafPath",
            "saveMetricLeafViaOp",
            "wtSaveMetricValue",
        ):
            self.assertIn("function " + name + "(", self.src)

    def test_setters_use_ops_helper(self):
        self.assertIn("wtSaveMetricValue(path", self.src)
        self.assertIn("wtDomainWriterSave(", self.src)
        # store / channel / shop metric / dept numeric — OPS leaf или atomic DomainWriter batch
        for fn in ("setShopStoreValue", "setShopChannelValue", "setShopMetricValue", "setDeptMetricValue"):
            start = self.src.find("function " + fn + "(")
            self.assertGreater(start, 0, fn)
            block = self.src[start : start + 4500]
            self.assertTrue(
                ("wtSaveMetricValue(" in block) or ("wtDomainWriterSave(" in block),
                fn + " must use OPS helper or DomainWriter",
            )

    def test_checklist_and_cashier_actual_still_put(self):
        for fn in ("setShopMetricCheck", "setDeptMetricCheck", "setShopCashierActual"):
            start = self.src.find("function " + fn + "(")
            self.assertGreater(start, 0, fn)
            nxt = self.src.find("\nfunction ", start + 10)
            block = self.src[start:nxt]
            self.assertIn("saveMetricChange(", block, fn)
            self.assertNotIn("wtSaveMetricValue(", block, fn)

    def test_vehicle_stays_on_put(self):
        start = self.src.find("function setDeptMetricValue(")
        block = self.src[start : self.src.find("\nfunction setDeptMetricCheck(", start)]
        self.assertIn('fill_type === "vehicle"', block)
        vehicle_part = block[block.find('fill_type === "vehicle"') : block.find("const n = parseShopMetricNumber")]
        self.assertIn("saveMetricChange(", vehicle_part)


@unittest.skipUnless(os.environ.get("WT_LIVE_BASE"), "WT_LIVE_BASE not set")
class WtMetricsOpLiveTests(unittest.TestCase):
    def setUp(self):
        self.base = os.environ["WT_LIVE_BASE"].rstrip("/")
        import http.cookiejar
        import urllib.request

        self.urllib = urllib.request
        self.cj = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cj))

    def req(self, method, path, body=None):
        data = None
        headers = {}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        r = self.urllib.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with self.opener.open(r) as resp:
                raw = resp.read().decode("utf-8") or "{}"
                return resp.status, json.loads(raw)
        except Exception as e:
            if hasattr(e, "code"):
                raw = e.read().decode("utf-8") or "{}"
                try:
                    return e.code, json.loads(raw)
                except Exception:
                    return e.code, {"raw": raw}
            raise

    def _op_write(self, path, value):
        st, d = self.req("POST", "/api/state/op", {"op": {"path": path, "expected_v": 0, "value": value}})
        if st == 409 and isinstance(d, dict) and d.get("server_v") is not None:
            st, d = self.req(
                "POST",
                "/api/state/op",
                {"op": {"path": path, "expected_v": int(d["server_v"]), "value": value}},
            )
        return st, d

    def test_live_two_leaves_no_global_conflict(self):
        st, health = self.req("GET", "/api/health")
        self.assertEqual(st, 200)
        self.assertTrue(health.get("ops"))
        st, _ = self.req("POST", "/api/login", {"user_id": "user_admin", "pin": "5555"})
        self.assertEqual(st, 200)
        p1 = "shopMetrics.store.2026-10.16.visitors"
        p2 = "shopMetrics.facts.2026-10.e_live16.16.metric_supplier_orders"
        st1, d1 = self._op_write(p1, 33)
        self.assertEqual(st1, 200, d1)
        st2, d2 = self._op_write(p2, 44)
        self.assertEqual(st2, 200, d2)
        self.assertNotEqual(d1.get("path"), d2.get("path"))
        st, state = self.req("GET", "/api/state")
        self.assertEqual(st, 200)
        sm = (state.get("payload") or {}).get("shopMetrics") or {}
        self.assertEqual(sm.get("store", {}).get("2026-10", {}).get("16", {}).get("visitors"), 33)
        self.assertEqual(
            sm.get("facts", {}).get("2026-10", {}).get("e_live16", {}).get("16", {}).get("metric_supplier_orders"),
            44,
        )


if __name__ == "__main__":
    unittest.main()
