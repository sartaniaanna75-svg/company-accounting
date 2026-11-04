# -*- coding: utf-8 -*-
"""
Финконтроль: резервный PUT и операции (financeRec.operations) — по finance_operation_id,
без физического удаления и сопоставления по индексу; soft-delete через is_deleted;
правка поверх более новой серверной версии — 409. Администратор и менеджер. FakeConn.
"""

from __future__ import annotations

import unittest

import wt_api
from test_fr_put_audit_guard import FrPutGuardBase, WHO


def _op(oid, amount, **extra):
    row = {"finance_operation_id": oid, "amount": amount, "is_deleted": False}
    row.update(extra)
    return row


class FrPutOperationsGuardTests(FrPutGuardBase):
    def ops(self):
        return [
            (o.get("finance_operation_id"), o.get("amount"), bool(o.get("is_deleted")))
            for o in self.store["payload"]["financeRec"]["operations"]
        ]

    def seed_two(self):
        self.store["payload"]["financeRec"]["operations"].append(_op("fop_b", 200))

    def test_stale_put_does_not_delete_or_overwrite_by_index(self):
        for who, uid, pin in WHO:
            with self.subTest(who=who):
                self.reset_store()
                self.seed_two()
                c = self.login(uid, pin)
                fr = self.fr()
                fr["operations"] = [_op("fop_b", 200), _op("fop_new", 7)]
                st, data = self.put(c, fr, self.meta())
                self.assertEqual(st, "200", data)
                self.assertEqual(self.ops(), [("fop_a", 100, False), ("fop_b", 200, False), ("fop_new", 7, False)])
                fr = self.fr()
                fr["operations"] = []
                st, data = self.put(c, fr, self.meta())
                self.assertEqual(st, "200", data)
                self.assertEqual([o for o, _, _ in self.ops()], ["fop_a", "fop_b", "fop_new"])

    def test_create_edit_soft_delete_with_current_version(self):
        for who, uid, pin in WHO:
            with self.subTest(who=who):
                self.reset_store()
                c = self.login(uid, pin)
                fr = self.fr()
                fr["operations"].append(_op("fop_c", 5))
                st, _ = self.put(c, fr, self.meta())
                self.assertEqual(st, "200")
                self.assertGreaterEqual(wt_api.record_version(self.store["payload"], "financeRec.operations.fop_c"), 1)
                fr = self.fr()
                fr["operations"][1]["amount"] = 6
                st, data = self.put(c, fr, self.meta())
                self.assertEqual(st, "200", data)
                fr = self.fr()
                fr["operations"][1]["is_deleted"] = True
                fr["operations"][1]["deleted_by"] = uid
                st, data = self.put(c, fr, self.meta())
                self.assertEqual(st, "200", data)
                self.assertEqual(self.ops(), [("fop_a", 100, False), ("fop_c", 6, True)])

    def test_edit_over_newer_server_version_is_conflict(self):
        for who, uid, pin in WHO:
            with self.subTest(who=who):
                self.reset_store()
                c = self.login(uid, pin)
                adm = self.login("user_admin", "5555")
                stale_fr, stale_meta = self.fr(), self.meta()
                v0 = int(self.store["version"])
                st, *_ = self.op(adm, "financeRec.operations.fop_a", _op("fop_a", 150), 0)
                self.assertTrue(st.startswith("200"))
                stale_fr["operations"][0]["amount"] = 120
                stale_fr["audit"].append({"audit_id": "faud_put", "kind": "op_edit"})
                st, data = self.put(c, stale_fr, stale_meta)
                self.assertEqual(st, "409", data)
                self.assertEqual(data.get("path"), "financeRec.operations")
                self.assertEqual(self.ops(), [("fop_a", 150, False)])
                self.assertNotIn("faud_put", [a for a, _ in self.audit()])
                self.assertEqual(int(self.store["version"]), v0 + 1)

    def test_soft_delete_over_newer_server_version_is_conflict(self):
        c = self.login("usr_dir", "4444")
        adm = self.login("user_admin", "5555")
        stale_fr, stale_meta = self.fr(), self.meta()
        self.op(adm, "financeRec.operations.fop_a", _op("fop_a", 150), 0)
        stale_fr["operations"][0]["is_deleted"] = True
        st, data = self.put(c, stale_fr, stale_meta)
        self.assertEqual(st, "409", data)
        self.assertEqual(self.ops(), [("fop_a", 150, False)])

    def test_two_users_different_operations(self):
        a = self.login("user_admin", "5555")
        m = self.login("usr_dir", "4444")
        self.seed_two()
        st1, *_ = self.op(a, "financeRec.operations.fop_a", _op("fop_a", 101), 0)
        st2, *_ = self.op(m, "financeRec.operations.fop_b", _op("fop_b", 202), 0)
        self.assertTrue(st1.startswith("200") and st2.startswith("200"))
        # Резервный PUT менеджера со свежим снимком: обе правки на месте, своя новая добавлена.
        fr = self.fr()
        fr["operations"].append(_op("fop_m", 3))
        st, data = self.put(m, fr, self.meta())
        self.assertEqual(st, "200", data)
        self.assertEqual(self.ops(), [("fop_a", 101, False), ("fop_b", 202, False), ("fop_m", 3, False)])

    def test_duplicate_and_missing_ids(self):
        for who, uid, pin in WHO:
            with self.subTest(who=who):
                self.reset_store()
                c = self.login(uid, pin)
                fr = self.fr()
                fr["operations"] += [_op("fop_d", 1), _op("fop_d", 2)]
                st, data = self.put(c, fr, self.meta())
                self.assertEqual(st, "409", data)
                fr = self.fr()
                fr["operations"].append({"amount": 9, "is_deleted": False})
                st, data = self.put(c, fr, self.meta())
                self.assertEqual(st, "409", data)
                self.assertEqual(self.ops(), [("fop_a", 100, False)])


if __name__ == "__main__":
    unittest.main()
