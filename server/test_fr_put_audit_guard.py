# -*- coding: utf-8 -*-
"""
Финконтроль: широкий PUT не удаляет и не меняет записи журнала (financeRec.audit).
Только добавление новых audit_id. Администратор и менеджер с financeRec=edit. FakeConn.
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

MGR = {
    "user_id": "usr_dir", "display_name": "Финменеджер", "pin": "4444", "role": "manager",
    "is_active": True, "departments": ["shop"],
    "permissions": {"financeRec": "edit", "financeRecSetup": "edit"},
}
SETUP_ONLY = {
    "user_id": "usr_setup", "display_name": "Справочник", "pin": "4810", "role": "user",
    "is_active": True, "departments": ["shop"],
    "permissions": {"financeRecSetup": "edit"},
}
WHO = (("admin", "user_admin", "5555"), ("manager", "usr_dir", "4444"))


class FrPutGuardBase(unittest.TestCase):
    def setUp(self):
        self._env_backup = dict(os.environ)
        os.environ["DATABASE_URL"] = "postgresql://worktime:" + SECRET + "@127.0.0.1:5432/worktime"
        os.environ["WT_PIN_PEPPER"] = "unit-pepper"
        os.environ["WT_SCRYPT_N"] = "16"
        os.environ["WT_OPS_ENABLED"] = "1"
        os.environ.pop("WT_API_TOKEN", None)
        wt_api.LOGIN_FAILS.clear()
        self.reset_store()
        self._patch = patch.object(wt_api, "connect", side_effect=lambda: FakeConn(self.store))
        self._patch.start()

    def reset_store(self):
        payload = json.loads(json.dumps(SAMPLE_APP))
        payload["users"].append(json.loads(json.dumps(MGR)))
        payload["users"].append(json.loads(json.dumps(SETUP_ONLY)))
        payload["financeRec"] = {
            "operations": [{"finance_operation_id": "fop_a", "amount": 100, "is_deleted": False}],
            "import_batches": [{"import_batch_id": "fimp_old", "batch_key": "old"}],
            "audit": [
                {"audit_id": "faud_old", "kind": "seed"},
                {"audit_id": "faud_old2", "kind": "seed2"},
            ],
            "openings": [],
            "period_states": [],
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

    def tearDown(self):
        self._patch.stop()
        os.environ.clear()
        os.environ.update(self._env_backup)
        wt_api.LOGIN_FAILS.clear()

    def login(self, user_id, pin):
        st, _, _, cookies = call("POST", "/api/login", {"user_id": user_id, "pin": pin})
        self.assertTrue(st.startswith("200"), st)
        return cookie_from(cookies)

    def fr(self):
        return json.loads(json.dumps(self.store["payload"]["financeRec"]))

    def meta(self):
        return json.loads(json.dumps(self.store["payload"].get("_wt_records") or {}))

    def put(self, cookie, fr, records=None):
        pl = {"financeRec": fr}
        if records:
            pl["_wt_records"] = records
        st, data, *_ = call("PUT", "/api/state", {"version": int(self.store["version"]), "payload": pl}, cookie=cookie)
        return st[:3], data or {}

    def op(self, cookie, path, value, expected_v=0):
        return call("POST", "/api/state/op", {"op": {"path": path, "expected_v": expected_v, "value": value}}, cookie=cookie)

    def audit(self):
        return [(a.get("audit_id"), a.get("kind")) for a in self.store["payload"]["financeRec"]["audit"]]


class FrPutAuditGuardTests(FrPutGuardBase):
    def test_delete_existing_entry_is_blocked(self):
        for who, uid, pin in WHO:
            with self.subTest(who=who):
                self.reset_store()
                c = self.login(uid, pin)
                self.op(c, "financeRec.audit.faud_x1", {"audit_id": "faud_x1", "kind": "via_ops"})
                for drop in ("faud_old", "faud_x1"):
                    fr = self.fr()
                    fr["audit"] = [a for a in fr["audit"] if a["audit_id"] != drop]
                    st, _ = self.put(c, fr, self.meta())
                    self.assertEqual(st, "200")
                    self.assertEqual(
                        self.audit(),
                        [("faud_old", "seed"), ("faud_old2", "seed2"), ("faud_x1", "via_ops")],
                        (who, drop),
                    )
    def test_modify_existing_entry_is_conflict(self):
        for who, uid, pin in WHO:
            with self.subTest(who=who):
                self.reset_store()
                c = self.login(uid, pin)
                v0 = int(self.store["version"])
                fr = self.fr()
                fr["audit"][0]["kind"] = "HACKED"
                fr["audit"].append({"audit_id": "faud_new", "kind": "new"})
                fr["operations"].append({"finance_operation_id": "fop_put", "amount": 1, "is_deleted": False})
                st, data = self.put(c, fr, self.meta())
                self.assertEqual(st, "409", data)
                self.assertEqual(data.get("error"), "conflict")
                self.assertEqual(data.get("path"), "financeRec.audit")
                # Пакет PUT отклонён целиком: ни журнал, ни операции не записаны.
                self.assertEqual(self.audit(), [("faud_old", "seed"), ("faud_old2", "seed2")])
                self.assertNotIn("fop_put", [o["finance_operation_id"] for o in self.store["payload"]["financeRec"]["operations"]])
                self.assertEqual(int(self.store["version"]), v0)
    def test_put_cannot_bypass_ops_immutability(self):
        for who, uid, pin in WHO:
            with self.subTest(who=who):
                self.reset_store()
                c = self.login(uid, pin)
                st, data, *_ = self.op(c, "financeRec.audit.faud_old", {"audit_id": "faud_old", "kind": "HACKED"})
                self.assertTrue(st.startswith("400"), (st, data))
                fr = self.fr()
                fr["audit"][0]["kind"] = "HACKED"
                st, data = self.put(c, fr, self.meta())
                self.assertEqual(st, "409", data)
                self.assertEqual(self.audit()[0], ("faud_old", "seed"))
    def test_stale_copy_keeps_server_entries_and_adds_own(self):
        for who, uid, pin in WHO:
            with self.subTest(who=who):
                self.reset_store()
                c = self.login(uid, pin)
                adm = self.login("user_admin", "5555")
                stale_fr, stale_meta = self.fr(), self.meta()
                self.op(adm, "financeRec.audit.faud_new", {"audit_id": "faud_new", "kind": "other_user"})
                # Устаревшая копия, другой порядок, без faud_old.
                stale_fr["audit"] = [{"audit_id": "faud_mine", "kind": "mine"}, {"audit_id": "faud_old2", "kind": "seed2"}]
                st, data = self.put(c, stale_fr, stale_meta)
                self.assertEqual(st, "200", data)
                self.assertEqual(self.audit(), [
                    ("faud_old", "seed"), ("faud_old2", "seed2"), ("faud_new", "other_user"), ("faud_mine", "mine"),
                ])
                # Новая запись получила версию: следующий OPS по ней — create-only.
                self.assertGreaterEqual(wt_api.record_version(self.store["payload"], "financeRec.audit.faud_mine"), 1)
    def test_new_entries_unique_and_no_duplicates(self):
        for who, uid, pin in WHO:
            with self.subTest(who=who):
                self.reset_store()
                c = self.login(uid, pin)
                fr = self.fr()
                fr["audit"].append({"audit_id": "faud_n1", "kind": "a"})
                fr["audit"].append({"audit_id": "faud_n1", "kind": "a"})
                st, _ = self.put(c, fr, self.meta())
                self.assertEqual(st, "200")
                ids = [a for a, _ in self.audit()]
                self.assertEqual(ids, ["faud_old", "faud_old2", "faud_n1"])
                fr = self.fr()
                fr["audit"].append({"audit_id": "faud_n2", "kind": "a"})
                fr["audit"].append({"audit_id": "faud_n2", "kind": "b"})
                st, data = self.put(c, fr, self.meta())
                self.assertEqual(st, "409", data)
                fr = self.fr()
                fr["audit"].append({"kind": "no_id"})
                st, data = self.put(c, fr, self.meta())
                self.assertEqual(st, "409", data)
                self.assertEqual([a for a, _ in self.audit()], ["faud_old", "faud_old2", "faud_n1"])
    def test_partial_put_without_audit_keeps_journal(self):
        for who, uid, pin in WHO:
            with self.subTest(who=who):
                self.reset_store()
                c = self.login(uid, pin)
                fr = self.fr()
                fr.pop("audit")
                st, _ = self.put(c, fr, self.meta())
                self.assertEqual(st, "200")
                self.assertEqual(self.audit(), [("faud_old", "seed"), ("faud_old2", "seed2")])
    def test_setup_only_user_cannot_add_audit(self):
        c = self.login("usr_setup", "4810")
        fr = self.fr()
        fr["audit"].append({"audit_id": "faud_setup", "kind": "x"})
        st, _ = self.put(c, {"audit": fr["audit"]}, self.meta())
        self.assertIn(st, ("200", "403"))
        self.assertNotIn("faud_setup", [a for a, _ in self.audit()])


if __name__ == "__main__":
    unittest.main()
