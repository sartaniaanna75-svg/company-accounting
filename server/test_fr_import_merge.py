# -*- coding: utf-8 -*-
"""Целевые тесты merge импорта расходников (зеркало frMergeFinanceArrayByIds)."""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

import test_wt_api as tw


FR_MOD = Path(__file__).resolve().parents[1].joinpath("fr-mod.js")

BUSINESS = {
    "operations": [
        "amount", "date", "payee", "comment", "operation", "op_time",
        "object_id", "finance_area_id", "account_id", "finance_source_id",
        "is_deleted", "type", "op_type",
    ],
    "import_batches": [
        "batch_key", "file_name", "finance_area_id", "object_id",
        "finance_source_id", "account_id", "data_source",
    ],
    "audit": ["kind", "finance_area_id", "finance_source_id", "import_batch_id"],
}


def _row_id(kind, row):
    if kind == "operations":
        return str(row.get("finance_operation_id") or "")
    if kind == "import_batches":
        return str(row.get("import_batch_id") or "")
    if kind == "audit":
        return str(row.get("audit_id") or "")
    return ""


def _row_fp(kind, row):
    if kind == "operations" and row.get("import_fingerprint"):
        return str(row["import_fingerprint"])
    if kind == "import_batches" and row.get("batch_key"):
        return str(row["batch_key"])
    return ""


def _norm(kind, field, value):
    if value is None:
        return ""
    if kind == "operations" and field == "amount":
        try:
            return str(float(value))
        except (TypeError, ValueError):
            return str(value)
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    return str(value).strip()


def _biz_diffs(kind, a, b):
    skip = {"account_id", "finance_source_id", "object_id", "finance_area_id"}
    diffs = []
    for field in BUSINESS.get(kind, []):
        if kind in ("operations", "import_batches") and field in skip:
            continue
        av = _norm(kind, field, (a or {}).get(field))
        bv = _norm(kind, field, (b or {}).get(field))
        if av != bv:
            diffs.append(field)
    if kind in ("operations", "import_batches"):
        a_acc = _norm(kind, "account_id", (a or {}).get("account_id") or (a or {}).get("finance_source_id"))
        b_acc = _norm(kind, "account_id", (b or {}).get("account_id") or (b or {}).get("finance_source_id"))
        if a_acc != b_acc:
            diffs.append("account_id")
        a_obj = _norm(kind, "object_id", (a or {}).get("object_id") or (a or {}).get("finance_area_id"))
        b_obj = _norm(kind, "object_id", (b or {}).get("object_id") or (b or {}).get("finance_area_id"))
        if a_obj != b_obj:
            diffs.append("object_id")
    return diffs


def merge(kind, remote_list, local_extras):
    """Зеркало frMergeFinanceArrayByIds после исправления ложного hardConflict."""
    remote = list(remote_list or [])
    by_id, by_fp = {}, {}
    for i, row in enumerate(remote):
        if not row:
            continue
        rid, fp = _row_id(kind, row), _row_fp(kind, row)
        if rid:
            by_id[rid] = i
        if fp:
            by_fp[fp] = i
    hard = False
    conflict = None
    for row in local_extras or []:
        if not row or hard:
            continue
        rid, fp = _row_id(kind, row), _row_fp(kind, row)
        if rid and rid in by_id:
            cur = remote[by_id[rid]]
            biz = _biz_diffs(kind, cur, row)
            if biz:
                hard = True
                conflict = {
                    "kind": kind,
                    "match_by": "id",
                    "record_id": rid,
                    "diff_fields": biz,
                    "business_conflict": True,
                }
            continue
        if fp and fp in by_fp:
            continue
        remote.append(row)
        idx = len(remote) - 1
        if rid:
            by_id[rid] = idx
        if fp:
            by_fp[fp] = idx
    return {"list": remote, "hardConflict": hard, "conflict": conflict}


class FrImportMergeUnitTests(unittest.TestCase):
    def test_fr_mod_has_business_diff_helpers(self):
        src = FR_MOD.read_text(encoding="utf-8")
        self.assertIn("function frFinanceBusinessDiffFields(", src)
        self.assertIn("FR_IMPORT_MERGE_TECH_FIELDS", src)
        self.assertIn("bizDiffs.length", src)
        self.assertIn("if (fp && Object.prototype.hasOwnProperty.call(byFp, fp)) return;", src)
        # Fingerprint alone must not suppress business conflict.
        self.assertNotIn("sameImportFp)", src[src.find("function frMergeFinanceArrayByIds("):])

    def test_batch_tech_only_no_hard_conflict(self):
        remote = [{
            "import_batch_id": "fimp_x",
            "batch_key": "bk1",
            "file_name": "a.xlsx",
            "finance_area_id": "a1",
            "object_id": "a1",
            "account_id": "acc1",
            "at": "t1",
            "rows_added": 0,
            "rows_dup": 0,
        }]
        local = [{
            "import_batch_id": "fimp_x",
            "batch_key": "bk1",
            "file_name": "a.xlsx",
            "finance_area_id": "a1",
            "object_id": "a1",
            "account_id": "acc1",
            "at": "t2",
            "rows_added": 3,
            "rows_dup": 1,
        }]
        r = merge("import_batches", remote, local)
        self.assertFalse(r["hardConflict"])
        self.assertEqual(r["list"][0]["rows_added"], 0)  # серверная история

    def test_batch_different_file_hard_conflict(self):
        remote = [{
            "import_batch_id": "fimp_x",
            "batch_key": "bk1",
            "file_name": "a.xlsx",
            "finance_area_id": "a1",
            "account_id": "acc1",
        }]
        local = [{
            "import_batch_id": "fimp_x",
            "batch_key": "bk1",
            "file_name": "b.xlsx",
            "finance_area_id": "a1",
            "account_id": "acc1",
        }]
        r = merge("import_batches", remote, local)
        self.assertTrue(r["hardConflict"])
        self.assertIn("file_name", r["conflict"]["diff_fields"])

    def test_ops_tech_stamp_no_hard_conflict(self):
        remote = [{
            "finance_operation_id": "op1",
            "import_fingerprint": "fp1",
            "amount": 100,
            "date": "2026-10-01",
            "payee": "P",
            "comment": "P",
            "account_id": "acc",
            "finance_source_id": "acc",
            "object_id": "area",
            "created_at": "t1",
        }]
        local = [{
            "finance_operation_id": "op1",
            "import_fingerprint": "fp1",
            "amount": 100,
            "date": "2026-10-01",
            "payee": "P",
            "comment": "P",
            "account_id": "acc",
            "finance_source_id": "acc",
            "finance_area_id": "area",
            "created_at": "t2",
            "data_source": "excel",
        }]
        r = merge("operations", remote, local)
        self.assertFalse(r["hardConflict"])
        self.assertEqual(r["list"][0]["created_at"], "t1")

    def test_ops_same_fp_but_amount_diff_hard_conflict(self):
        """Fingerprint не спасает при расхождении суммы."""
        remote = [{
            "finance_operation_id": "op1",
            "import_fingerprint": "fp_same",
            "amount": 100,
            "date": "2026-10-01",
            "payee": "P",
            "account_id": "acc",
            "object_id": "area",
        }]
        local = [{
            "finance_operation_id": "op1",
            "import_fingerprint": "fp_same",
            "amount": 999,
            "date": "2026-10-01",
            "payee": "P",
            "account_id": "acc",
            "object_id": "area",
        }]
        r = merge("operations", remote, local)
        self.assertTrue(r["hardConflict"])
        self.assertIn("amount", r["conflict"]["diff_fields"])

    def test_ops_date_change_hard_conflict(self):
        remote = [{
            "finance_operation_id": "op1",
            "amount": 10,
            "date": "2026-10-01",
            "payee": "P",
            "account_id": "acc",
            "object_id": "a",
        }]
        local = [dict(remote[0], date="2026-10-02")]
        r = merge("operations", remote, local)
        self.assertTrue(r["hardConflict"])
        self.assertIn("date", r["conflict"]["diff_fields"])

    def test_same_fp_different_id_no_duplicate(self):
        remote = [{
            "finance_operation_id": "op1",
            "import_fingerprint": "fp_same",
            "amount": 10,
            "date": "2026-10-01",
            "payee": "P",
            "account_id": "acc",
            "object_id": "a",
        }]
        local = [{
            "finance_operation_id": "op2",
            "import_fingerprint": "fp_same",
            "amount": 10,
            "date": "2026-10-01",
            "payee": "P",
            "account_id": "acc",
            "object_id": "a",
        }]
        r = merge("operations", remote, local)
        self.assertFalse(r["hardConflict"])
        self.assertEqual(len(r["list"]), 1)

    def test_two_departments_no_conflict(self):
        remote = [{
            "finance_operation_id": "op_shop",
            "import_fingerprint": "fp_shop",
            "amount": 10,
            "date": "2026-10-01",
            "object_id": "shop",
            "account_id": "acc_s",
        }]
        local = [{
            "finance_operation_id": "op_ter",
            "import_fingerprint": "fp_ter",
            "amount": 20,
            "date": "2026-10-01",
            "object_id": "ter",
            "account_id": "acc_t",
        }]
        r = merge("operations", remote, local)
        self.assertFalse(r["hardConflict"])
        self.assertEqual(len(r["list"]), 2)


class FrImportMergeE2ETests(unittest.TestCase):
    """Сквозной путь: merge → OPS save → GET (повторное чтение)."""

    def setUp(self):
        # Тот же fixture, что StoreApiTests, без наследования всех его test_* .
        self._env_backup = dict(os.environ)
        os.environ["DATABASE_URL"] = "postgresql://worktime:" + tw.SECRET + "@127.0.0.1:5432/worktime"
        os.environ["WT_PIN_PEPPER"] = "unit-pepper"
        os.environ["WT_SCRYPT_N"] = "16"
        os.environ.pop("WT_API_TOKEN", None)
        os.environ.pop("WT_COOKIE_SECURE", None)
        tw.wt_api.LOGIN_FAILS.clear()
        self.store = {
            "payload": json.loads(json.dumps(tw.SAMPLE_APP)),
            "version": 0,
            "updated_at": None,
            "updated_by": "",
            "fail": False,
            "sessions": {},
        }
        self._patch = patch.object(tw.wt_api, "connect", side_effect=lambda: tw.FakeConn(self.store))
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        os.environ.clear()
        os.environ.update(self._env_backup)

    def _seed_finance_directory(self):
        helper = tw.StoreApiTests()
        helper.store = self.store
        helper._seed_finance_directory()

    def add_user(self, user):
        self.store["payload"]["users"].append(json.loads(json.dumps(user)))

    def login(self, user_id="user_admin", pin="5555"):
        status, data, _, cookies = tw.call("POST", "/api/login", {"user_id": user_id, "pin": pin})
        self.assertTrue(status.startswith("200"), status)
        return tw.cookie_from(cookies), data

    def test_e2e_retry_after_orphan_and_partial_then_f5(self):
        self._seed_finance_directory()
        fr = self.store["payload"]["financeRec"]
        # 3) Orphan batch без операций
        fr["operations"] = []
        fr["import_batches"] = [{
            "import_batch_id": "fimp_e2e",
            "batch_key": "key_e2e",
            "file_name": "exp.xlsx",
            "finance_area_id": "farea_shop",
            "object_id": "farea_shop",
            "account_id": "fsrc_shop_a",
            "finance_source_id": "fsrc_shop_a",
            "data_source": "excel",
            "at": "2026-10-01T10:00:00Z",
            "rows_added": 0,
            "rows_dup": 0,
        }]
        fr["audit"] = []
        self.store["payload"]["_wt_records"] = {
            "financeRec.import_batches": {"v": 1},
            "financeRec.operations": {"v": 0},
            "financeRec.audit": {"v": 0},
        }
        self.add_user({
            "user_id": "fin_e2e",
            "display_name": "E2E",
            "pin": "3099",
            "role": "user",
            "is_active": True,
            "departments": ["financeRec"],
            "permissions": {"financeRec": "edit"},
        })
        cookie, _ = self.login("fin_e2e", "3099")

        # Локальный черновик после неудачного сохранения: тот же batch id, техполя другие
        local_batch = [{
            "import_batch_id": "fimp_e2e",
            "batch_key": "key_e2e",
            "file_name": "exp.xlsx",
            "finance_area_id": "farea_shop",
            "object_id": "farea_shop",
            "account_id": "fsrc_shop_a",
            "finance_source_id": "fsrc_shop_a",
            "data_source": "excel",
            "at": "2026-10-01T10:05:00Z",
            "rows_added": 2,
            "rows_dup": 0,
        }]
        local_ops = [
            {
                "finance_operation_id": "fop_e2e_1",
                "import_fingerprint": "fp_e2e_1",
                "import_batch_id": "fimp_e2e",
                "amount": 15,
                "date": "2026-10-20",
                "payee": "A",
                "comment": "A",
                "object_id": "farea_shop",
                "finance_area_id": "farea_shop",
                "account_id": "fsrc_shop_a",
                "finance_source_id": "fsrc_shop_a",
                "is_deleted": False,
                "created_at": "t-local",
            },
            {
                "finance_operation_id": "fop_e2e_2",
                "import_fingerprint": "fp_e2e_2",
                "import_batch_id": "fimp_e2e",
                "amount": 25,
                "date": "2026-10-20",
                "payee": "B",
                "comment": "B",
                "object_id": "farea_shop",
                "finance_area_id": "farea_shop",
                "account_id": "fsrc_shop_a",
                "finance_source_id": "fsrc_shop_a",
                "is_deleted": False,
            },
        ]

        # 2/5) Merge: tech drift batch + новые ops — без hardConflict
        mb = merge("import_batches", fr["import_batches"], local_batch)
        mo = merge("operations", fr["operations"], local_ops)
        self.assertFalse(mb["hardConflict"], mb)
        self.assertFalse(mo["hardConflict"], mo)
        self.assertEqual(mb["list"][0]["rows_added"], 0)  # не раздуваем серверную историю локально
        self.assertEqual(len(mo["list"]), 2)

        # 1) Первая успешная запись через OPS batch
        stg, dg, *_ = tw.call("GET", "/api/state", cookie=cookie)
        self.assertTrue(stg.startswith("200"), stg)
        rec = (dg.get("payload") or {}).get("_wt_records") or {}
        st, *_ = tw.call(
            "POST",
            "/api/state/op/batch",
            {
                "atomic": True,
                "ops": [
                    {
                        "path": "financeRec.operations",
                        "expected_v": int((rec.get("financeRec.operations") or {}).get("v") or 0),
                        "value": mo["list"],
                    },
                    {
                        "path": "financeRec.import_batches",
                        "expected_v": int((rec.get("financeRec.import_batches") or {}).get("v") or 0),
                        "value": mb["list"],
                    },
                ],
            },
            cookie=cookie,
        )
        self.assertTrue(st.startswith("200"), st)

        # 4) Частичный импорт: на сервере уже fp_e2e_1; локально «добавляем» оба + техполя
        saved = self.store["payload"]["financeRec"]
        # имитация частичного: оставляем только первую
        saved["operations"] = [dict(mo["list"][0])]
        self.store["payload"]["_wt_records"]["financeRec.operations"] = {
            "v": int(self.store["payload"]["_wt_records"]["financeRec.operations"]["v"]) + 1
        }
        # Повтор: merge remote partial + local full set (новый id для второй уже есть)
        mo2 = merge("operations", saved["operations"], local_ops)
        self.assertFalse(mo2["hardConflict"])
        fps = {str(o.get("import_fingerprint")) for o in mo2["list"]}
        self.assertEqual(fps, {"fp_e2e_1", "fp_e2e_2"})
        self.assertEqual(len(mo2["list"]), 2)  # без дубля fp_e2e_1

        stg2, dg2, *_ = tw.call("GET", "/api/state", cookie=cookie)
        v_ops = int((((dg2.get("payload") or {}).get("_wt_records") or {})
                     .get("financeRec.operations") or {}).get("v") or 0)
        st2, *_ = tw.call(
            "POST",
            "/api/state/op",
            {"op": {"path": "financeRec.operations", "expected_v": v_ops, "value": mo2["list"]}},
            cookie=cookie,
        )
        self.assertTrue(st2.startswith("200"), st2)

        # 6) Реальное изменение суммы той же id → hardConflict
        remote_ops = list(self.store["payload"]["financeRec"]["operations"])
        edited = [dict(remote_ops[0], amount=999)]
        bad = merge("operations", remote_ops, edited)
        self.assertTrue(bad["hardConflict"])
        self.assertIn("amount", bad["conflict"]["diff_fields"])

        # 7) Другой отдел — без конфликта
        other = [{
            "finance_operation_id": "fop_ter",
            "import_fingerprint": "fp_ter",
            "amount": 5,
            "date": "2026-10-20",
            "object_id": "farea_territory",
            "finance_area_id": "farea_territory",
            "account_id": "fsrc_ter",
        }]
        ok_dept = merge("operations", remote_ops, other)
        self.assertFalse(ok_dept["hardConflict"])
        self.assertEqual(len(ok_dept["list"]), len(remote_ops) + 1)

        # 8) F5 = GET: все операции на месте
        stg3, dg3, *_ = tw.call("GET", "/api/state", cookie=cookie)
        self.assertTrue(stg3.startswith("200"), stg3)
        ops = (dg3["payload"]["financeRec"].get("operations") or [])
        fps3 = {str(o.get("import_fingerprint")) for o in ops if o and not o.get("is_deleted")}
        self.assertEqual(fps3, {"fp_e2e_1", "fp_e2e_2"})

        # 9) Неполный expected set ≠ complete success
        expected = ["fp_e2e_1", "fp_e2e_2", "fp_e2e_3"]
        missing = [fp for fp in expected if fp not in fps3]
        complete = len(expected) > 0 and len(missing) == 0
        self.assertFalse(complete)
        self.assertEqual(missing, ["fp_e2e_3"])


if __name__ == "__main__":
    unittest.main()
