# -*- coding: utf-8 -*-
"""
Финконтроль: широкий PUT и партии импорта (financeRec.import_batches) — по import_batch_id,
без удаления, без дублей, без сопоставления по индексу; правка чужой более новой версии — 409.
Администратор и менеджер с financeRec=edit. FakeConn.
"""

from __future__ import annotations

import unittest

import wt_api
from test_fr_put_audit_guard import FrPutGuardBase, WHO


class FrPutBatchesGuardTests(FrPutGuardBase):
    def batches(self):
        return [
            (b.get("import_batch_id"), b.get("batch_key"), b.get("rows_added"))
            for b in self.store["payload"]["financeRec"]["import_batches"]
        ]

    def seed_two(self):
        self.store["payload"]["financeRec"]["import_batches"].append(
            {"import_batch_id": "fimp_b2", "batch_key": "k2", "rows_added": 2}
        )

    def test_stale_put_does_not_delete_or_duplicate_by_index(self):
        for who, uid, pin in WHO:
            with self.subTest(who=who):
                self.reset_store()
                self.seed_two()
                c = self.login(uid, pin)
                fr = self.fr()
                fr["import_batches"] = [{"import_batch_id": "fimp_b2", "batch_key": "k2", "rows_added": 2}]
                st, data = self.put(c, fr, self.meta())
                self.assertEqual(st, "200", data)
                self.assertEqual(self.batches(), [("fimp_old", "old", None), ("fimp_b2", "k2", 2)])

    def test_two_users_new_batches_both_kept(self):
        for who, uid, pin in WHO:
            with self.subTest(who=who):
                self.reset_store()
                c = self.login(uid, pin)
                adm = self.login("user_admin", "5555")
                stale_fr, stale_meta = self.fr(), self.meta()
                st, *_ = self.op(adm, "financeRec.import_batches.fimp_other", {"import_batch_id": "fimp_other", "batch_key": "ko"})
                self.assertTrue(st.startswith("200"))
                stale_fr["import_batches"].append({"import_batch_id": "fimp_mine", "batch_key": "km"})
                st, data = self.put(c, stale_fr, stale_meta)
                self.assertEqual(st, "200", data)
                ids = [b for b, _, _ in self.batches()]
                self.assertEqual(ids, ["fimp_old", "fimp_other", "fimp_mine"])

    def test_edit_with_current_version_applies(self):
        for who, uid, pin in WHO:
            with self.subTest(who=who):
                self.reset_store()
                c = self.login(uid, pin)
                st, *_ = self.op(c, "financeRec.import_batches.fimp_old", {"import_batch_id": "fimp_old", "batch_key": "old", "rows_added": 3}, 0)
                self.assertTrue(st.startswith("200"))
                fr = self.fr()
                fr["import_batches"][0]["rows_added"] = 5
                st, data = self.put(c, fr, self.meta())
                self.assertEqual(st, "200", data)
                self.assertEqual(self.batches(), [("fimp_old", "old", 5)])

    def test_edit_over_newer_server_version_is_conflict(self):
        for who, uid, pin in WHO:
            with self.subTest(who=who):
                self.reset_store()
                c = self.login(uid, pin)
                adm = self.login("user_admin", "5555")
                stale_fr, stale_meta = self.fr(), self.meta()
                v0 = int(self.store["version"])
                st, *_ = self.op(adm, "financeRec.import_batches.fimp_old", {"import_batch_id": "fimp_old", "batch_key": "old", "rows_added": 9}, 0)
                self.assertTrue(st.startswith("200"))
                stale_fr["import_batches"][0]["rows_added"] = 4
                stale_fr["operations"].append({"finance_operation_id": "fop_put", "amount": 1, "is_deleted": False})
                st, data = self.put(c, stale_fr, stale_meta)
                self.assertEqual(st, "409", data)
                self.assertEqual(data.get("path"), "financeRec.import_batches")
                self.assertEqual(self.batches(), [("fimp_old", "old", 9)])
                self.assertNotIn("fop_put", [o["finance_operation_id"] for o in self.store["payload"]["financeRec"]["operations"]])
                self.assertEqual(int(self.store["version"]), v0 + 1)

    def test_duplicate_new_batch_ids(self):
        for who, uid, pin in WHO:
            with self.subTest(who=who):
                self.reset_store()
                c = self.login(uid, pin)
                fr = self.fr()
                fr["import_batches"] += [{"import_batch_id": "fimp_n", "batch_key": "a"}] * 2
                st, _ = self.put(c, fr, self.meta())
                self.assertEqual(st, "200")
                self.assertEqual([b for b, _, _ in self.batches()], ["fimp_old", "fimp_n"])
                fr = self.fr()
                fr["import_batches"] += [
                    {"import_batch_id": "fimp_m", "batch_key": "a"},
                    {"import_batch_id": "fimp_m", "batch_key": "b"},
                ]
                st, data = self.put(c, fr, self.meta())
                self.assertEqual(st, "409", data)
                self.assertEqual([b for b, _, _ in self.batches()], ["fimp_old", "fimp_n"])

    def test_new_batch_gets_record_version(self):
        c = self.login("usr_dir", "4444")
        fr = self.fr()
        fr["import_batches"].append({"import_batch_id": "fimp_v", "batch_key": "kv"})
        st, _ = self.put(c, fr, self.meta())
        self.assertEqual(st, "200")
        self.assertGreaterEqual(wt_api.record_version(self.store["payload"], "financeRec.import_batches.fimp_v"), 1)


if __name__ == "__main__":
    unittest.main()
