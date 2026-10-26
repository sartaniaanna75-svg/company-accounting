# -*- coding: utf-8 -*-
"""
Повторная синхронизация одного пути: 100→200→null→300, текст, marks, dual-session.
FakeConn + wiring app.html (SoftAppliedVersion / focusout / null-delete).
"""
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


class WtResyncWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = APP_HTML.read_text(encoding="utf-8")

    def test_soft_applied_version_always_on_apply(self):
        start = self.src.find("function wtSoftApplyServerState(")
        block = self.src[start : self.src.find("\nfunction wtSoftRefreshUiAfterMerge(", start)]
        # Больше не откладываем SoftAppliedVersion из‑за active edit.
        self.assertIn("wtSoftAppliedVersion = appliedV", block)
        self.assertNotIn("if (!wtSoftHasActiveEdit()) wtSoftAppliedVersion = appliedV", block)
        self.assertIn("wtSoftOnFocusOut", self.src)
        self.assertIn('document.addEventListener("focusout", wtSoftOnFocusOut, true)', self.src)

    def test_set_by_path_null_deletes(self):
        start = self.src.find("function wtSetByPath(")
        block = self.src[start : self.src.find("\nfunction wtNewIdempotencyKey(", start)]
        self.assertGreaterEqual(block.count("value === undefined || value === null"), 3)


class WtResyncRepeatApiTests(unittest.TestCase):
    def setUp(self):
        self._env = dict(os.environ)
        os.environ["DATABASE_URL"] = "postgresql://worktime:" + SECRET + "@127.0.0.1:5432/worktime"
        os.environ["WT_PIN_PEPPER"] = "unit-pepper"
        os.environ["WT_SCRYPT_N"] = "16"
        os.environ["WT_OPS_ENABLED"] = "1"
        os.environ.pop("WT_API_TOKEN", None)
        wt_api.LOGIN_FAILS.clear()
        payload = json.loads(json.dumps(SAMPLE_APP))
        payload["shopMetrics"] = {
            "facts": {"2026-10": {"e_shop": {"5": {}}}},
            "pickers": {"2026-10": {"e_shop": {"5": {}}}},
            "store": {"2026-10": {"5": {}}},
        }
        payload["salary"] = {"manual": {"2026-10": {"e_shop": {"note": "A"}}}}
        self.store = {
            "payload": payload,
            "version": 1,
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
        os.environ.update(self._env)
        wt_api.LOGIN_FAILS.clear()

    def login(self, uid="user_admin", pin="5555"):
        st, _, _, cookies = call("POST", "/api/login", {"user_id": uid, "pin": pin})
        self.assertTrue(st.startswith("200"), st)
        return cookie_from(cookies)

    def op(self, cookie, path, value, expected_v):
        return call(
            "POST",
            "/api/state/op",
            {"op": {"path": path, "expected_v": expected_v, "value": value}},
            cookie=cookie,
        )

    def test_number_sequence_100_200_null_300(self):
        c = self.login()
        path = "shopMetrics.store.2026-10.5.visitors"
        seq = [100, 200, None, 300]
        ev = 0
        for val in seq:
            st, data, *_ = self.op(c, path, val, ev)
            self.assertTrue(st.startswith("200"), (val, st, data))
            ev = int(data.get("record_v") or ev + 1)
            got = wt_api.get_path_value(self.store["payload"], path)
            self.assertEqual(got, val)

    def test_text_sequence_and_delete(self):
        c = self.login()
        path = "salary.manual.2026-10.e_shop.note"
        # baseline A at v0
        st, data, *_ = self.op(c, path, "A", 0)
        self.assertTrue(st.startswith("200"), (st, data))
        ev = int(data["record_v"])
        for val in ("B", None, "C"):
            st, data, *_ = self.op(c, path, val, ev)
            self.assertTrue(st.startswith("200"), (val, st, data))
            ev = int(data["record_v"])
            self.assertEqual(wt_api.get_path_value(self.store["payload"], path), val)

    def test_mark_toggle_sequence(self):
        c = self.login()
        path = "shopMetrics.pickers.2026-10.e_shop.5.displayMark"
        ev = 0
        for val in ("done", "fail", "done", None):
            st, data, *_ = self.op(c, path, val, ev)
            self.assertTrue(st.startswith("200"), (val, st, data))
            ev = int(data["record_v"])
            self.assertEqual(wt_api.get_path_value(self.store["payload"], path), val)

    def test_dual_session_repeat_same_path_conflict_then_overwrite(self):
        a = self.login("user_admin", "5555")
        b = self.login("usr_001", "1111")
        path = "shopMetrics.store.2026-10.5.visitors"
        st, data, *_ = self.op(a, path, 10, 0)
        self.assertTrue(st.startswith("200"))
        ev = int(data["record_v"])
        st2, data2, *_ = self.op(b, path, 20, 0)
        self.assertTrue(st2.startswith("409"), (st2, data2))
        st3, data3, *_ = self.op(b, path, 20, ev)
        self.assertTrue(st3.startswith("200"), (st3, data3))
        self.assertEqual(wt_api.get_path_value(self.store["payload"], path), 20)
        # ещё одно изменение тем же путём
        ev2 = int(data3["record_v"])
        st4, data4, *_ = self.op(a, path, 30, ev2)
        self.assertTrue(st4.startswith("200"), (st4, data4))
        self.assertEqual(wt_api.get_path_value(self.store["payload"], path), 30)

    def test_batch_repeat_null_then_value(self):
        c = self.login()
        path = "shopMetrics.facts.2026-10.e_shop.5.metric_supplier_orders"
        st, data, *_ = call(
            "POST",
            "/api/state/op/batch",
            {
                "atomic": True,
                "ops": [
                    {"path": path, "expected_v": 0, "value": 100},
                    {
                        "path": "shopMetrics.pickers.2026-10.e_shop.5.recount",
                        "expected_v": 0,
                        "value": 100,
                    },
                ],
            },
            cookie=c,
        )
        self.assertTrue(st.startswith("200"), (st, data))
        rv = int(data["results"][0]["record_v"])
        rv2 = int(data["results"][1]["record_v"])
        st, data, *_ = call(
            "POST",
            "/api/state/op/batch",
            {
                "atomic": True,
                "ops": [
                    {"path": path, "expected_v": rv, "value": None},
                    {
                        "path": "shopMetrics.pickers.2026-10.e_shop.5.recount",
                        "expected_v": rv2,
                        "value": None,
                    },
                ],
            },
            cookie=c,
        )
        self.assertTrue(st.startswith("200"), (st, data))
        self.assertIsNone(wt_api.get_path_value(self.store["payload"], path))
        st, data, *_ = self.op(c, path, 300, int(data["results"][0]["record_v"]))
        self.assertTrue(st.startswith("200"), (st, data))
        self.assertEqual(wt_api.get_path_value(self.store["payload"], path), 300)


if __name__ == "__main__":
    unittest.main()
