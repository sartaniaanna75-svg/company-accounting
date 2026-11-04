# -*- coding: utf-8 -*-
"""
OPS-адресация financeRec.money_transits.<transit_id> (массив остаётся массивом).
FakeConn, искусственные данные, без live / Timeweb.
"""

from __future__ import annotations

import json
import os
import threading
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import wt_api
from test_wt_api import SAMPLE_APP, FakeConn, SECRET, call, cookie_from
from test_fr_close_period_atomic import MGR


def _transit(tid, sent=700, closed=0, **extra):
    row = {
        "transit_id": tid, "object_id": "farea_shop", "finance_area_id": "farea_shop",
        "from_account_id": "fsrc_cash", "transit_account_id": "fsrc_transit",
        "amount_sent": sent, "amount_closed": closed, "amount_open": sent - closed,
        "status": "open" if not closed else "partial", "closes": [],
    }
    row.update(extra)
    return row


class FrMoneyTransitsOpsTests(unittest.TestCase):
    def setUp(self):
        self._env_backup = dict(os.environ)
        os.environ["DATABASE_URL"] = "postgresql://worktime:" + SECRET + "@127.0.0.1:5432/worktime"
        os.environ["WT_PIN_PEPPER"] = "unit-pepper"
        os.environ["WT_SCRYPT_N"] = "16"
        os.environ["WT_OPS_ENABLED"] = "1"
        os.environ.pop("WT_API_TOKEN", None)
        wt_api.LOGIN_FAILS.clear()
        payload = json.loads(json.dumps(SAMPLE_APP))
        payload["users"].append(json.loads(json.dumps(MGR)))
        legacy = _transit("", sent=40)
        legacy.pop("transit_id")
        payload["financeRec"] = {
            "operations": [{"finance_operation_id": "fop_a", "amount": 100, "is_deleted": False}],
            "audit": [{"audit_id": "faud_old", "kind": "seed"}],
            # ftr_1, ftr_2 — обычные; третий — старый транзит без transit_id.
            "money_transits": [_transit("ftr_1"), _transit("ftr_2", sent=300), legacy],
            "sources": [],
            "areas": [],
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
        st, data, _, cookies = call("POST", "/api/login", {"user_id": user_id, "pin": pin})
        self.assertTrue(st.startswith("200"), st)
        return cookie_from(cookies)

    def op(self, cookie, path, value, expected_v=0):
        return call("POST", "/api/state/op", {"op": {"path": path, "expected_v": expected_v, "value": value}}, cookie=cookie)

    def batch(self, cookie, ops, atomic=True):
        return call("POST", "/api/state/op/batch", {"ops": ops, "atomic": atomic}, cookie=cookie)

    def transits(self):
        return self.store["payload"]["financeRec"]["money_transits"]

    def by_id(self, tid):
        return next((t for t in self.transits() if t.get("transit_id") == tid), None)

    def meta_v(self, path):
        meta = (self.store["payload"].get("_wt_records") or {}).get(path) or {}
        return int(meta.get("v") or 0)

    def test_read_create_and_edit_by_id(self):
        cookie = self.login()
        st, data, *_ = call("GET", "/api/state", cookie=cookie)
        self.assertTrue(st.startswith("200"))
        self.assertEqual(wt_api.get_path_value(data["payload"], "financeRec.money_transits.ftr_2")["amount_sent"], 300)
        st, data, *_ = self.op(cookie, "financeRec.money_transits.ftr_new", _transit("ftr_wrong", sent=55), 0)
        self.assertTrue(st.startswith("200"), (st, data))
        self.assertIsInstance(self.transits(), list)
        self.assertEqual(len(self.transits()), 4)
        # id берётся из пути — дубля под другим id нет.
        self.assertEqual(self.by_id("ftr_new")["amount_sent"], 55)
        self.assertIsNone(self.by_id("ftr_wrong"))
        v1 = self.meta_v("financeRec.money_transits.ftr_new")
        self.assertGreater(v1, 0)
        edited = dict(self.by_id("ftr_new"), amount_closed=20)
        st, data, *_ = self.op(cookie, "financeRec.money_transits.ftr_new", edited, v1)
        self.assertTrue(st.startswith("200"), (st, data))
        self.assertEqual(self.by_id("ftr_new")["amount_closed"], 20)
        self.assertGreater(self.meta_v("financeRec.money_transits.ftr_new"), v1)

    def test_other_transits_and_legacy_row_untouched(self):
        cookie = self.login()
        before = json.loads(json.dumps(self.transits()))
        st, data, *_ = self.op(cookie, "financeRec.money_transits.ftr_1", dict(before[0], amount_closed=100), 0)
        self.assertTrue(st.startswith("200"), (st, data))
        after = self.transits()
        self.assertEqual(len(after), 3)
        self.assertEqual(after[1], before[1])
        self.assertEqual(after[2], before[2])
        self.assertNotIn("transit_id", after[2])
        self.assertEqual(after[0]["amount_closed"], 100)

    def test_stale_version_is_conflict(self):
        a = self.login()
        b = self.login("usr_dir", "4444")
        base = json.loads(json.dumps(self.by_id("ftr_1")))
        st, *_ = self.op(b, "financeRec.money_transits.ftr_1", dict(base, amount_closed=300), 0)
        self.assertTrue(st.startswith("200"), st)
        st, data, *_ = self.op(a, "financeRec.money_transits.ftr_1", dict(base, amount_closed=200), 0)
        self.assertTrue(st.startswith("409"), (st, data))
        self.assertEqual(data.get("path"), "financeRec.money_transits.ftr_1")
        self.assertEqual(self.by_id("ftr_1")["amount_closed"], 300)

    def test_create_with_existing_id_is_conflict(self):
        cookie = self.login()
        st, *_ = self.op(cookie, "financeRec.money_transits.ftr_x", _transit("ftr_x", sent=10), 0)
        self.assertTrue(st.startswith("200"))
        st, data, *_ = self.op(cookie, "financeRec.money_transits.ftr_x", _transit("ftr_x", sent=99), 0)
        self.assertTrue(st.startswith("409"), (st, data))
        self.assertEqual([t.get("transit_id") for t in self.transits()].count("ftr_x"), 1)
        self.assertEqual(self.by_id("ftr_x")["amount_sent"], 10)

    def test_null_delete_rejected(self):
        cookie = self.login()
        v = self.meta_v("financeRec.money_transits.ftr_1")
        st, data, *_ = self.op(cookie, "financeRec.money_transits.ftr_1", None, v)
        self.assertFalse(st.startswith("200"), (st, data))
        self.assertIn("транзит", (data.get("message") or "").lower())
        self.assertIsNotNone(self.by_id("ftr_1"))

    def test_field_edit_of_missing_transit_rejected(self):
        cookie = self.login()
        st, data, *_ = self.op(cookie, "financeRec.money_transits.ftr_missing.amount_closed", 5, 0)
        self.assertFalse(st.startswith("200"), (st, data))
        self.assertIsNone(self.by_id("ftr_missing"))

    def test_child_write_bumps_root_and_stale_wide_write_conflicts(self):
        cookie = self.login()
        root_v = self.meta_v("financeRec.money_transits")
        stale_list = json.loads(json.dumps(self.transits()))
        st, *_ = self.op(cookie, "financeRec.money_transits.ftr_new", _transit("ftr_new"), 0)
        self.assertTrue(st.startswith("200"))
        self.assertGreater(self.meta_v("financeRec.money_transits"), root_v)
        st, data, *_ = self.op(cookie, "financeRec.money_transits", stale_list, root_v)
        self.assertTrue(st.startswith("409"), (st, data))
        self.assertIsNotNone(self.by_id("ftr_new"))

    def test_atomic_batch_op_transit_audit_all_or_nothing(self):
        cookie = self.login()
        good = [
            {"path": "financeRec.operations.fop_ts", "expected_v": 0,
             "value": {"finance_operation_id": "fop_ts", "amount": 700, "is_deleted": False, "transit_id": "ftr_n"}},
            {"path": "financeRec.money_transits.ftr_n", "expected_v": 0, "value": _transit("ftr_n", send_operation_id="fop_ts")},
            {"path": "financeRec.audit.faud_ts", "expected_v": 0, "value": {"audit_id": "faud_ts", "kind": "transit_send"}},
        ]
        # Транзит ftr_1 уже изменён другим сотрудником → 2-я часть пакета устарела.
        st, *_ = self.op(cookie, "financeRec.money_transits.ftr_1", dict(self.by_id("ftr_1"), amount_closed=1), 0)
        self.assertTrue(st.startswith("200"))
        bad2 = json.loads(json.dumps(good))
        bad2[1]["path"] = "financeRec.money_transits.ftr_1"
        bad2[1]["value"]["transit_id"] = "ftr_1"
        # 3-я часть: повтор существующего audit_id с другим содержимым.
        bad3 = json.loads(json.dumps(good))
        bad3[2]["path"] = "financeRec.audit.faud_old"
        bad3[2]["value"]["audit_id"] = "faud_old"
        for bad in (bad2, bad3):
            st, data, *_ = self.batch(cookie, bad)
            self.assertFalse(st.startswith("200"), (st, data))
            fr = self.store["payload"]["financeRec"]
            self.assertNotIn("fop_ts", [o.get("finance_operation_id") for o in fr["operations"]])
            self.assertNotIn("faud_ts", [a.get("audit_id") for a in fr["audit"]])
            self.assertIsNone(self.by_id("ftr_n"))
            self.assertEqual(self.by_id("ftr_1")["amount_closed"], 1)
            self.assertEqual(next(a for a in fr["audit"] if a["audit_id"] == "faud_old"), {"audit_id": "faud_old", "kind": "seed"})
        st, data, *_ = self.batch(cookie, good)
        self.assertTrue(st.startswith("200"), (st, data))
        fr = self.store["payload"]["financeRec"]
        self.assertEqual(self.by_id("ftr_n")["send_operation_id"], "fop_ts")
        self.assertIn("fop_ts", [o.get("finance_operation_id") for o in fr["operations"]])
        self.assertIn("faud_ts", [a.get("audit_id") for a in fr["audit"]])
        self.assertEqual(len(self.transits()), 4)

    def test_manager_can_write_transit_by_id(self):
        cookie = self.login("usr_dir", "4444")
        st, data, *_ = self.op(cookie, "financeRec.money_transits.ftr_m", _transit("ftr_m"), 0)
        self.assertTrue(st.startswith("200"), (st, data))
        self.assertIsNotNone(self.by_id("ftr_m"))
        self.assertEqual(len(self.transits()), 4)


if __name__ == "__main__":
    unittest.main()
