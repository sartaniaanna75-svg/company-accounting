# -*- coding: utf-8 -*-
"""
OPS-адресация financeRec.operations.<finance_operation_id> (массив остаётся массивом).
FakeConn, без live / Timeweb. UI не затрагивается.
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


class WtFrOpsByIdTests(unittest.TestCase):
    def setUp(self):
        self._env_backup = dict(os.environ)
        os.environ["DATABASE_URL"] = "postgresql://worktime:" + SECRET + "@127.0.0.1:5432/worktime"
        os.environ["WT_PIN_PEPPER"] = "unit-pepper"
        os.environ["WT_SCRYPT_N"] = "16"
        os.environ["WT_OPS_ENABLED"] = "1"
        os.environ.pop("WT_API_TOKEN", None)
        wt_api.LOGIN_FAILS.clear()
        payload = json.loads(json.dumps(SAMPLE_APP))
        payload["financeRec"] = {
            "operations": [
                {
                    "finance_operation_id": "fop_a",
                    "amount": 100,
                    "comment": "A",
                    "is_deleted": False,
                },
                {
                    "finance_operation_id": "fop_b",
                    "amount": 200,
                    "comment": "B",
                    "is_deleted": False,
                },
            ],
            "openings": [{"finance_source_id": "fsrc_1", "value": 1}],
            "audit": [],
            "sources": [],
            "areas": [],
        }
        # Для регресса users / vacation / employees.
        payload["vacationPlans"] = {
            "2026": [
                {
                    "vacation_plan_id": "vp_1",
                    "employee_id": "e_shop",
                    "planned_date": "2026-11-10",
                    "planned_amount": 0,
                    "status": "planned",
                }
            ]
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

    def op(self, cookie, path, value, expected_v=0, idem=None):
        body = {"op": {"path": path, "expected_v": expected_v, "value": value}}
        if idem:
            body["idempotency_key"] = idem
        return call("POST", "/api/state/op", body, cookie=cookie)

    def batch(self, cookie, ops, atomic=True, idem=None):
        body = {"ops": ops, "atomic": atomic}
        if idem:
            body["idempotency_key"] = idem
        return call("POST", "/api/state/op/batch", body, cookie=cookie)

    def _ops(self):
        return self.store["payload"]["financeRec"]["operations"]

    def test_create_operation_keeps_array(self):
        cookie = self.login()
        st, data, *_ = self.op(
            cookie,
            "financeRec.operations.fop_c",
            {
                "finance_operation_id": "fop_wrong",
                "amount": 50,
                "comment": "C",
                "is_deleted": False,
            },
            expected_v=0,
            idem="fr-create-c",
        )
        self.assertTrue(st.startswith("200"), (st, data))
        ops = self._ops()
        self.assertIsInstance(ops, list)
        self.assertEqual(len(ops), 3)
        hit = next(o for o in ops if o.get("finance_operation_id") == "fop_c")
        self.assertEqual(hit["amount"], 50)
        # ID из пути побеждает тело.
        self.assertEqual(hit["finance_operation_id"], "fop_c")
        self.assertEqual(self.store["payload"]["financeRec"]["openings"][0]["value"], 1)

    def test_update_whole_operation(self):
        cookie = self.login()
        st, data, *_ = self.op(
            cookie,
            "financeRec.operations.fop_a",
            {
                "finance_operation_id": "fop_a",
                "amount": 150,
                "comment": "A2",
                "is_deleted": False,
            },
            expected_v=0,
        )
        self.assertTrue(st.startswith("200"), (st, data))
        ops = self._ops()
        self.assertEqual(len(ops), 2)
        self.assertEqual(ops[0]["amount"], 150)
        self.assertEqual(ops[0]["comment"], "A2")
        self.assertEqual(ops[1]["amount"], 200)

    def test_update_single_field(self):
        cookie = self.login()
        st, data, *_ = self.op(
            cookie,
            "financeRec.operations.fop_b.amount",
            777,
            expected_v=0,
        )
        self.assertTrue(st.startswith("200"), (st, data))
        ops = self._ops()
        self.assertEqual(ops[1]["amount"], 777)
        self.assertEqual(ops[1]["comment"], "B")
        self.assertEqual(ops[0]["amount"], 100)

    def test_soft_delete_via_is_deleted_not_null(self):
        cookie = self.login()
        st_null, d_null, *_ = self.op(
            cookie,
            "financeRec.operations.fop_a",
            None,
            expected_v=0,
        )
        self.assertTrue(st_null.startswith("400"), (st_null, d_null))
        self.assertEqual(d_null.get("error"), "invalid value")
        self.assertEqual(self._ops()[0]["is_deleted"], False)

        st, data, *_ = self.op(
            cookie,
            "financeRec.operations.fop_a",
            {
                "finance_operation_id": "fop_a",
                "amount": 100,
                "comment": "A",
                "is_deleted": True,
            },
            expected_v=0,
        )
        self.assertTrue(st.startswith("200"), (st, data))
        self.assertTrue(self._ops()[0]["is_deleted"])
        self.assertEqual(len(self._ops()), 2)

    def test_field_update_missing_id_rejected(self):
        cookie = self.login()
        before = json.loads(json.dumps(self._ops()))
        st, data, *_ = self.op(
            cookie,
            "financeRec.operations.fop_missing.amount",
            1,
            expected_v=0,
        )
        self.assertTrue(st.startswith("400"), (st, data))
        self.assertEqual(data.get("error"), "invalid path")
        self.assertEqual(self._ops(), before)

    def test_empty_id_rejected(self):
        cookie = self.login()
        # Путь с пустым сегментом не проходит normalize — ожидаем 400 invalid path.
        st, data, *_ = call(
            "POST",
            "/api/state/op",
            {"op": {"path": "financeRec.operations..amount", "expected_v": 0, "value": 1}},
            cookie=cookie,
        )
        self.assertTrue(st.startswith("400"), (st, data))

    def test_idempotent_recreate_same_id_no_duplicate(self):
        cookie = self.login()
        body = {
            "finance_operation_id": "fop_c",
            "amount": 1,
            "comment": "once",
            "is_deleted": False,
        }
        st1, *_ = self.op(cookie, "financeRec.operations.fop_c", body, 0, idem="fr-dup-1")
        self.assertTrue(st1.startswith("200"), st1)
        # Повтор с тем же ID — upsert, не второй элемент (новая версия пути).
        st2, data2, *_ = self.op(
            cookie,
            "financeRec.operations.fop_c",
            dict(body, amount=2, comment="twice"),
            expected_v=1,
            idem="fr-dup-2",
        )
        self.assertTrue(st2.startswith("200"), (st2, data2))
        ids = [o.get("finance_operation_id") for o in self._ops()]
        self.assertEqual(ids.count("fop_c"), 1)
        hit = next(o for o in self._ops() if o.get("finance_operation_id") == "fop_c")
        self.assertEqual(hit["amount"], 2)

    def test_two_users_different_operations(self):
        a = self.login("user_admin", "5555")
        # Второй пользователь с financeRec.edit
        payload_users = self.store["payload"]["users"]
        payload_users.append(
            {
                "user_id": "usr_fin2",
                "display_name": "Fin2",
                "pin": "6666",
                "role": "user",
                "is_active": True,
                "departments": ["summary"],
                "permissions": {"financeRec": "edit"},
            }
        )
        # Нужен pin_hash — login через API с plaintext только если hash_pins... SAMPLE использует pin в login.
        b = self.login("usr_fin2", "6666")
        results = {}

        def job_a():
            st, d, *_ = self.op(a, "financeRec.operations.fop_a.amount", 111, 0, idem="a-amt")
            results["a"] = (st, d)

        def job_b():
            st, d, *_ = self.op(b, "financeRec.operations.fop_b.amount", 222, 0, idem="b-amt")
            results["b"] = (st, d)

        t1 = threading.Thread(target=job_a)
        t2 = threading.Thread(target=job_b)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        self.assertTrue(results["a"][0].startswith("200"), results["a"])
        self.assertTrue(results["b"][0].startswith("200"), results["b"])
        by_id = {o["finance_operation_id"]: o for o in self._ops()}
        self.assertEqual(by_id["fop_a"]["amount"], 111)
        self.assertEqual(by_id["fop_b"]["amount"], 222)

    def test_same_operation_conflict_409(self):
        a = self.login("user_admin", "5555")
        self.store["payload"]["users"].append(
            {
                "user_id": "usr_fin3",
                "display_name": "Fin3",
                "pin": "7777",
                "role": "user",
                "is_active": True,
                "departments": ["summary"],
                "permissions": {"financeRec": "edit"},
            }
        )
        b = self.login("usr_fin3", "7777")
        st1, *_ = self.op(a, "financeRec.operations.fop_a.amount", 11, 0, idem="same-a")
        self.assertTrue(st1.startswith("200"), st1)
        st2, d2, *_ = self.op(b, "financeRec.operations.fop_a.amount", 22, 0, idem="same-b")
        self.assertTrue(st2.startswith("409"), (st2, d2))
        self.assertEqual(d2.get("error"), "conflict")
        self.assertEqual(d2.get("path"), "financeRec.operations.fop_a.amount")
        self.assertEqual(
            next(o for o in self._ops() if o["finance_operation_id"] == "fop_a")["amount"],
            11,
        )

    def test_atomic_batch_second_conflict_rolls_back(self):
        cookie = self.login()
        # Поднимаем версию fop_b.amount
        st0, *_ = self.op(cookie, "financeRec.operations.fop_b.amount", 201, 0, idem="pre-b")
        self.assertTrue(st0.startswith("200"), st0)
        st, data, *_ = self.batch(
            cookie,
            [
                {
                    "path": "financeRec.operations.fop_a.amount",
                    "expected_v": 0,
                    "value": 999,
                },
                {
                    "path": "financeRec.operations.fop_b.amount",
                    "expected_v": 0,  # устарело
                    "value": 888,
                },
            ],
            atomic=True,
            idem="fr-atomic-fail",
        )
        self.assertTrue(st.startswith("409"), (st, data))
        by_id = {o["finance_operation_id"]: o for o in self._ops()}
        self.assertEqual(by_id["fop_a"]["amount"], 100)
        self.assertEqual(by_id["fop_b"]["amount"], 201)

    def test_forbidden_without_finance_edit(self):
        # STAFF без financeRec
        cookie = self.login("usr_001", "1111")
        st, data, *_ = self.op(cookie, "financeRec.operations.fop_a.amount", 1, 0)
        self.assertTrue(st.startswith("403"), (st, data))
        self.assertEqual(self._ops()[0]["amount"], 100)

    def test_regression_users_employees_vacation_still_work(self):
        cookie = self.login()
        st_u, *_ = self.op(
            cookie,
            "users.user_admin.display_name",
            "Админ FR",
            expected_v=0,
            idem="reg-user",
        )
        self.assertTrue(st_u.startswith("200"), st_u)
        st_e, *_ = self.op(
            cookie,
            "workspaces.shop.employees.e_shop.name",
            "Касса FR",
            expected_v=0,
            idem="reg-emp",
        )
        self.assertTrue(st_e.startswith("200"), st_e)
        st_v, *_ = self.op(
            cookie,
            "vacationPlans.2026.vp_1.status",
            "planned",
            expected_v=0,
            idem="reg-vp",
        )
        self.assertTrue(st_v.startswith("200"), st_v)
        # Массив operations не превратился в dict.
        self.assertIsInstance(self._ops(), list)
        self.assertEqual(len(self._ops()), 2)

    def test_legacy_dict_operations_path_still_works(self):
        """Старый тестовый shape operations:dict не ломаем."""
        self.store["payload"]["financeRec"]["operations"] = {
            "op1": {"amount": 100, "comment": "d"}
        }
        cookie = self.login()
        st, data, *_ = self.op(
            cookie,
            "financeRec.operations.op1.amount",
            555,
            expected_v=0,
            idem="legacy-dict",
        )
        self.assertTrue(st.startswith("200"), (st, data))
        self.assertEqual(self.store["payload"]["financeRec"]["operations"]["op1"]["amount"], 555)


class WtFrMoneyOpsClientMarkersTests(unittest.TestCase):
    """Клиент 2Б: узкие OPS по id, без широкого saveApp в денежных handlers."""

    def _src(self):
        return Path(__file__).resolve().parents[1].joinpath("fr-mod.js").read_text(encoding="utf-8")

    def _fn(self, src, name, end_name):
        start = src.find("function " + name + "(")
        end = src.find("function " + end_name + "(", start + 1)
        self.assertGreater(start, -1, name)
        self.assertGreater(end, start, end_name)
        return src[start:end]

    def test_persist_helper_uses_op_batch_no_force_put(self):
        src = self._src()
        self.assertIn("function frPersistOpsById(", src)
        fn = self._fn(src, "frPersistOpsById", "frAccountUsage")
        self.assertIn('"/api/state/op/batch"', fn)
        self.assertIn("financeRec.operations.", fn)
        self.assertIn("wtSoftMarkDirty", fn)
        self.assertNotIn("forcePut", fn)
        self.assertIn("широкая перезапись", fn)

    def test_money_handlers_use_persist_not_wide_saveapp(self):
        src = self._src()
        for name, end in (
            ("saveFrOp", "frDeleteOp"),
            ("frDeleteOp", "frOnSourceDragStart"),
            ("frQuickAddOp", "frQuickAddRefund"),
            ("frQuickAddRefund", "renderFrDaysTab"),
            ("frCommitRevenueCell", "frParsePasteMatrix"),
            ("frCommitNonIncomeCell", "frOnNonIncomePaste"),
            ("frCommitNonIncomeComment", "frCommitNonIncomeCell"),
        ):
            fn = self._fn(src, name, end)
            # frDeleteOp (этап 4А): операция + транзит по id через frPersistOpsWithTransits.
            persist_call = "frPersistOpsWithTransits(" if name == "frDeleteOp" else "frPersistOpsById("
            self.assertIn(persist_call, fn, name)
            self.assertNotIn("saveApp();", fn, name)
        wrapper = self._fn(src, "frPersistOpsWithTransits", "frMoneyOpsBlocked")
        self.assertIn("frPersistOpsById(", wrapper)
        self.assertNotIn("saveApp();", wrapper)

    def test_paste_batches_via_queue(self):
        src = self._src()
        rev = self._fn(src, "frOnReconcilePaste", "frRefreshReconcileTotalsDom")
        self.assertIn("frFlushMoneyOpPasteQueue(", rev)
        self.assertIn("_reconcilePasteLock", rev)
        ni = self._fn(src, "frOnNonIncomePaste", "frRefreshNonIncomeTotalsDom")
        self.assertIn("frFlushMoneyOpPasteQueue(", ni)
        self.assertIn("_niPasteLock", ni)

    def test_save_fr_op_keeps_modal_until_success(self):
        fn = self._fn(self._src(), "saveFrOp", "frDeleteOp")
        self.assertIn("onSuccess", fn)
        self.assertIn("closeMgmtModal()", fn)
        self.assertLess(fn.find("frPersistOpsById("), fn.find("closeMgmtModal()"))

    def test_import_ops_by_id_refuses_whole_array_and_forceput(self):
        """Этап 3Б: нет whole_array / forcePut на импорте; too_large и ops_unavailable."""
        src = self._src()
        self.assertIn("FR_IMPORT_MAX_BATCH_OPS", src)
        self.assertIn("function frBuildExpenseImportOpsLeaves(", src)
        build = self._fn(src, "frBuildExpenseImportOpsLeaves", "frSaveFinanceImportArraysOps")
        self.assertIn('mode: "bulk"', build)
        self.assertIn('mode: "too_large"', build)
        self.assertIn("FR_IMPORT_BULK_PATH", build)
        self.assertIn('"financeRec.import_batches." + bid', build)
        self.assertIn('"financeRec.audit."', build)
        self.assertNotIn("whole_array", build)
        # Целые массивы operations / import_batches / audit больше не пушатся.
        self.assertNotIn('path: "financeRec.operations"', build)
        self.assertNotIn('path: "financeRec.import_batches"', build)
        self.assertNotIn('path: "financeRec.audit"', build)
        save = self._fn(src, "frSaveFinanceImportArraysOps", "frReconcileFailedExpenseImport")
        self.assertIn("frBuildExpenseImportOpsLeaves(batchId)", save)
        self.assertIn("ops_unavailable", save)
        self.assertNotIn("forcePut: true", save)
        self.assertNotIn("saveApp({ forcePut", save)
        batch = self._fn(src, "frSaveExpenseImportBatch", "financeRecFolderCardHtml")
        self.assertIn("ops_unavailable", batch)
        self.assertNotIn("forcePut: true", batch)
        self.assertNotIn("saveApp({ forcePut", batch)
        apply = self._fn(src, "frApplyExpenseImport", "frCollectExpenseImportDupKeys")
        self.assertIn("FR_IMPORT_BULK_MAX_ITEMS", apply)
        self.assertIn("wouldAdd", apply)
        self.assertIn('nextPrefixedUniqueId("fimp"', apply)

    def test_manual_audit_same_atomic_batch(self):
        """Этап 3В: ручная операция + запись журнала по audit_id одним atomic batch."""
        src = self._src()
        aud = self._fn(src, "frAudit", "frStatusLabel")
        self.assertIn('nextPrefixedUniqueId("faud"', aud)
        self.assertIn("function frAuditLeaf(", src)
        self.assertIn('"financeRec.audit." + id', src)
        for name, end in (
            ("saveFrOp", "frDeleteOp"),
            ("frDeleteOp", "frOnSourceDragStart"),
            ("frQuickAddOp", "frQuickAddRefund"),
            ("frQuickAddRefund", "renderFrDaysTab"),
            ("frCommitRevenueCell", "frParsePasteMatrix"),
            ("frCommitNonIncomeCell", "frOnNonIncomePaste"),
        ):
            fn = self._fn(src, name, end)
            if name == "frDeleteOp":
                self.assertIn("auditIds: [audId]", fn, name)
                continue
            self.assertIn("frAuditLeaf(audId)", fn, name)
            self.assertIn("createdAuditIds", fn, name)
        wrapper = self._fn(src, "frPersistOpsWithTransits", "frMoneyOpsBlocked")
        self.assertIn("frAuditLeaf(aid)", wrapper)
        self.assertIn("createdAuditIds: auditIds", wrapper)
        persist = self._fn(src, "frPersistOpsById", "frAccountUsage")
        self.assertIn("frDropLocalAuditIds(createdAuditIds)", persist)
        self.assertIn("frSnapshotApplyConfirmed(", persist)


class WtFrImportVsManualOpsTests(unittest.TestCase):
    """
    Этап 3: широкий financeRec.operations затирает ручной OPS-by-id;
    запись только batch-ops по id сохраняет чужую операцию.
    """

    def setUp(self):
        self._env_backup = dict(os.environ)
        os.environ["DATABASE_URL"] = "postgresql://worktime:" + SECRET + "@127.0.0.1:5432/worktime"
        os.environ["WT_PIN_PEPPER"] = "unit-pepper"
        os.environ["WT_SCRYPT_N"] = "16"
        os.environ["WT_OPS_ENABLED"] = "1"
        os.environ.pop("WT_API_TOKEN", None)
        wt_api.LOGIN_FAILS.clear()
        payload = json.loads(json.dumps(SAMPLE_APP))
        payload["financeRec"] = {
            "operations": [
                {"finance_operation_id": "fop_a", "amount": 1, "is_deleted": False},
                {"finance_operation_id": "fop_b", "amount": 2, "is_deleted": False},
            ],
            "import_batches": [],
            "audit": [],
        }
        self.store = {
            "payload": payload,
            "version": 2,
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

    def login(self):
        st, data, _, cookies = call("POST", "/api/login", {"user_id": "user_admin", "pin": "5555"})
        self.assertTrue(st.startswith("200"), st)
        return cookie_from(cookies)

    def _ids(self):
        return [o.get("finance_operation_id") for o in self.store["payload"]["financeRec"]["operations"]]

    def test_wide_array_import_wipes_manual_op(self):
        """Диагностика: целый массив + expected_v корня не видит OPS-by-id."""
        cookie = self.login()
        st1, *_ = call(
            "POST",
            "/api/state/op",
            {
                "op": {
                    "path": "financeRec.operations.fop_e",
                    "expected_v": 0,
                    "value": {
                        "finance_operation_id": "fop_e",
                        "amount": 99,
                        "is_deleted": False,
                    },
                }
            },
            cookie=cookie,
        )
        self.assertTrue(st1.startswith("200"), st1)
        self.assertIn("fop_e", self._ids())
        ev = wt_api.record_version(self.store["payload"], "financeRec.operations")
        stale = [
            {"finance_operation_id": "fop_a", "amount": 1, "is_deleted": False},
            {"finance_operation_id": "fop_b", "amount": 2, "is_deleted": False},
            {
                "finance_operation_id": "fop_c",
                "amount": 3,
                "import_batch_id": "fimp_1",
                "import_fingerprint": "fp_c",
                "is_deleted": False,
            },
        ]
        st2, *_ = call(
            "POST",
            "/api/state/op/batch",
            {
                "atomic": True,
                "ops": [
                    {"path": "financeRec.operations", "expected_v": ev, "value": stale},
                    {
                        "path": "financeRec.import_batches",
                        "expected_v": 0,
                        "value": [{"import_batch_id": "fimp_1", "batch_key": "k"}],
                    },
                ],
            },
            cookie=cookie,
        )
        self.assertTrue(st2.startswith("200"), st2)
        self.assertNotIn("fop_e", self._ids())
        self.assertIn("fop_c", self._ids())

    def test_import_by_id_keeps_manual_op(self):
        """Исправление: batch пишет только свои id — ручная fop_e остаётся."""
        cookie = self.login()
        st1, *_ = call(
            "POST",
            "/api/state/op",
            {
                "op": {
                    "path": "financeRec.operations.fop_e",
                    "expected_v": 0,
                    "value": {
                        "finance_operation_id": "fop_e",
                        "amount": 99,
                        "comment": "manual",
                        "is_deleted": False,
                    },
                }
            },
            cookie=cookie,
        )
        self.assertTrue(st1.startswith("200"), st1)
        st2, d2, *_ = call(
            "POST",
            "/api/state/op/batch",
            {
                "atomic": True,
                "ops": [
                    {
                        "path": "financeRec.operations.fop_c",
                        "expected_v": 0,
                        "value": {
                            "finance_operation_id": "fop_c",
                            "amount": 3,
                            "import_batch_id": "fimp_1",
                            "import_fingerprint": "fp_c",
                            "is_deleted": False,
                        },
                    },
                    {
                        "path": "financeRec.import_batches",
                        "expected_v": 0,
                        "value": [{"import_batch_id": "fimp_1", "batch_key": "k"}],
                    },
                    {"path": "financeRec.audit", "expected_v": 0, "value": []},
                ],
            },
            cookie=cookie,
        )
        self.assertTrue(st2.startswith("200"), (st2, d2))
        ids = self._ids()
        self.assertIn("fop_e", ids)
        self.assertIn("fop_c", ids)
        self.assertIn("fop_a", ids)
        manual = next(o for o in self.store["payload"]["financeRec"]["operations"] if o["finance_operation_id"] == "fop_e")
        self.assertEqual(manual["amount"], 99)
        self.assertEqual(manual.get("comment"), "manual")

    def test_import_198_by_id_atomic_ok(self):
        """198 ops + batches + audit = 200 листьев — в лимите сервера."""
        cookie = self.login()
        ops = []
        for i in range(198):
            oid = "fop_i%03d" % i
            ops.append({
                "path": "financeRec.operations." + oid,
                "expected_v": 0,
                "value": {
                    "finance_operation_id": oid,
                    "amount": i + 1,
                    "import_batch_id": "fimp_big",
                    "import_fingerprint": "fp_i%03d" % i,
                    "is_deleted": False,
                },
            })
        ops.append({
            "path": "financeRec.import_batches",
            "expected_v": 0,
            "value": [{"import_batch_id": "fimp_big", "batch_key": "kb"}],
        })
        ops.append({
            "path": "financeRec.audit",
            "expected_v": 0,
            "value": [{"audit_id": "faud_big", "kind": "expense_import", "import_batch_id": "fimp_big"}],
        })
        self.assertEqual(len(ops), 200)
        st, data, *_ = call(
            "POST",
            "/api/state/op/batch",
            {"atomic": True, "ops": ops},
            cookie=cookie,
        )
        self.assertTrue(st.startswith("200"), (st, data))
        ids = self._ids()
        self.assertIn("fop_a", ids)
        self.assertIn("fop_i000", ids)
        self.assertIn("fop_i197", ids)
        self.assertEqual(len([x for x in ids if str(x).startswith("fop_i")]), 198)

    def test_import_199_rejected_by_server_limit(self):
        """199 ops + 2 meta = 201 > 200 — сервер отклоняет; клиент тоже не шлёт whole_array."""
        cookie = self.login()
        ops = []
        for i in range(199):
            oid = "fop_j%03d" % i
            ops.append({
                "path": "financeRec.operations." + oid,
                "expected_v": 0,
                "value": {
                    "finance_operation_id": oid,
                    "amount": 1,
                    "import_batch_id": "fimp_over",
                    "is_deleted": False,
                },
            })
        ops.append({"path": "financeRec.import_batches", "expected_v": 0, "value": []})
        ops.append({"path": "financeRec.audit", "expected_v": 0, "value": []})
        self.assertEqual(len(ops), 201)
        st, data, *_ = call(
            "POST",
            "/api/state/op/batch",
            {"atomic": True, "ops": ops},
            cookie=cookie,
        )
        self.assertTrue(st.startswith("400") or (isinstance(data, dict) and data.get("error") == "too many ops"), (st, data))
        # Ничего из oversized batch не должно появиться.
        ids = self._ids()
        self.assertNotIn("fop_j000", ids)
        self.assertIn("fop_a", ids)

    def test_import_batches_whole_array_can_wipe_peer_batch(self):
        """Диагностика: целый financeRec.import_batches затирает чужую партию (как operations)."""
        cookie = self.login()
        st1, *_ = call(
            "POST",
            "/api/state/op",
            {
                "op": {
                    "path": "financeRec.import_batches",
                    "expected_v": 0,
                    "value": [
                        {"import_batch_id": "fimp_peer", "batch_key": "peer"},
                        {"import_batch_id": "fimp_old", "batch_key": "old"},
                    ],
                }
            },
            cookie=cookie,
        )
        self.assertTrue(st1.startswith("200"), st1)
        ev = wt_api.record_version(self.store["payload"], "financeRec.import_batches")
        # Стейл-клиент без fimp_peer, но с тем же expected_v корня… после peer-записи version вырос.
        # Сценарий wipe: писать целый массив без peer при expected_v ПОСЛЕ peer (как если meta не различает id).
        st2, *_ = call(
            "POST",
            "/api/state/op/batch",
            {
                "atomic": True,
                "ops": [
                    {
                        "path": "financeRec.import_batches",
                        "expected_v": ev,
                        "value": [{"import_batch_id": "fimp_mine", "batch_key": "mine"}],
                    }
                ],
            },
            cookie=cookie,
        )
        self.assertTrue(st2.startswith("200"), st2)
        bids = [b.get("import_batch_id") for b in self.store["payload"]["financeRec"]["import_batches"]]
        self.assertIn("fimp_mine", bids)
        self.assertNotIn("fimp_peer", bids)

    def test_atomic_mid_batch_failure_leaves_no_partial_ops(self):
        """Отказ посреди atomic batch: ни одной новой операции импорта."""
        cookie = self.login()
        before = list(self._ids())
        st, data, *_ = call(
            "POST",
            "/api/state/op/batch",
            {
                "atomic": True,
                "ops": [
                    {
                        "path": "financeRec.operations.fop_new1",
                        "expected_v": 0,
                        "value": {
                            "finance_operation_id": "fop_new1",
                            "amount": 1,
                            "import_batch_id": "fimp_x",
                            "is_deleted": False,
                        },
                    },
                    {
                        # Конфликт: существующий fop_a с неверной expected_v.
                        "path": "financeRec.operations.fop_a",
                        "expected_v": 999,
                        "value": {
                            "finance_operation_id": "fop_a",
                            "amount": 1,
                            "is_deleted": False,
                        },
                    },
                ],
            },
            cookie=cookie,
        )
        self.assertTrue(
            st.startswith("409") or (isinstance(data, dict) and data.get("error") in ("conflict", "version conflict")),
            (st, data),
        )
        self.assertEqual(self._ids(), before)
        self.assertNotIn("fop_new1", self._ids())


if __name__ == "__main__":
    unittest.main()
