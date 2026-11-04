# -*- coding: utf-8 -*-
"""
Этап 3В: financeRec.operations.$bulk, audit.<id>, import_batches.<id>.
FakeConn, без live / Timeweb.
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


def _op_item(oid, amount=1, expected_v=0, **extra):
    val = {
        "finance_operation_id": oid,
        "amount": amount,
        "is_deleted": False,
    }
    val.update(extra)
    return {"expected_v": expected_v, "value": val}


class WtFrOpsAbcTests(unittest.TestCase):
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
                {"finance_operation_id": "fop_a", "amount": 100, "is_deleted": False},
                {"finance_operation_id": "fop_b", "amount": 200, "is_deleted": False},
            ],
            "import_batches": [
                {"import_batch_id": "fimp_old", "batch_key": "old"},
            ],
            "audit": [
                {"audit_id": "faud_old", "kind": "seed"},
            ],
            "openings": [],
            "sources": [],
            "areas": [],
        }
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

    def batch(self, cookie, ops, atomic=True):
        return call(
            "POST",
            "/api/state/op/batch",
            {"ops": ops, "atomic": atomic},
            cookie=cookie,
        )

    def op(self, cookie, path, value, expected_v=0):
        return call(
            "POST",
            "/api/state/op",
            {"op": {"path": path, "expected_v": expected_v, "value": value}},
            cookie=cookie,
        )

    def _ids(self):
        return [o.get("finance_operation_id") for o in self.store["payload"]["financeRec"]["operations"]]

    def _bids(self):
        return [b.get("import_batch_id") for b in self.store["payload"]["financeRec"]["import_batches"]]

    def _aids(self):
        return [a.get("audit_id") for a in self.store["payload"]["financeRec"]["audit"]]

    def test_path_re_allows_dollar_bulk(self):
        self.assertEqual(
            wt_api.normalize_op_path("financeRec.operations.$bulk"),
            "financeRec.operations.$bulk",
        )
        self.assertIsNone(wt_api.normalize_op_path("financeRec.operations.$"))
        self.assertIsNone(wt_api.normalize_op_path("$bulk.foo"))

    def test_bulk_50_upsert_keeps_peers(self):
        cookie = self.login()
        items = [_op_item("fop_n%03d" % i, amount=i + 1) for i in range(50)]
        st, data, *_ = self.op(cookie, wt_api.FR_OPS_BULK_PATH, items, expected_v=0)
        self.assertTrue(st.startswith("200"), (st, data))
        ids = self._ids()
        self.assertIn("fop_a", ids)
        self.assertIn("fop_b", ids)
        self.assertEqual(len([x for x in ids if str(x).startswith("fop_n")]), 50)

    def test_bulk_198_199_500_1000(self):
        cookie = self.login()
        for n in (198, 199, 500, 1000):
            items = [_op_item("fop_%s_%04d" % (n, i), amount=1) for i in range(n)]
            ev = wt_api.record_version(self.store["payload"], wt_api.FR_OPS_BULK_PATH)
            st, data, *_ = self.op(cookie, wt_api.FR_OPS_BULK_PATH, items, expected_v=ev)
            self.assertTrue(st.startswith("200"), (n, st, data))
            ids = self._ids()
            self.assertIn("fop_%s_0000" % n, ids)
            self.assertIn("fop_%s_%04d" % (n, n - 1), ids)

    def test_bulk_rejects_over_max(self):
        cookie = self.login()
        n = wt_api.FR_OPS_BULK_MAX_ITEMS + 1
        items = [_op_item("fop_over_%04d" % i) for i in range(n)]
        st, data, *_ = self.op(cookie, wt_api.FR_OPS_BULK_PATH, items, expected_v=0)
        self.assertTrue(st.startswith("400") or (isinstance(data, dict) and data.get("error") == "too many ops"), (st, data))
        self.assertNotIn("fop_over_0000", self._ids())

    def test_bulk_duplicate_id_rejected(self):
        cookie = self.login()
        items = [_op_item("fop_dup", amount=1), _op_item("fop_dup", amount=2)]
        st, data, *_ = self.op(cookie, wt_api.FR_OPS_BULK_PATH, items, expected_v=0)
        self.assertTrue(
            st.startswith("400") or (isinstance(data, dict) and data.get("error") == "duplicate id"),
            (st, data),
        )
        self.assertNotIn("fop_dup", self._ids())

    def test_bulk_conflict_on_stale_op_version(self):
        cookie = self.login()
        st1, *_ = self.op(
            cookie,
            "financeRec.operations.fop_a",
            {"finance_operation_id": "fop_a", "amount": 111, "is_deleted": False},
            expected_v=0,
        )
        self.assertTrue(st1.startswith("200"), st1)
        # stale expected_v=0 for fop_a inside bulk
        items = [_op_item("fop_a", amount=999, expected_v=0), _op_item("fop_newx", amount=1)]
        st2, data2, *_ = self.op(cookie, wt_api.FR_OPS_BULK_PATH, items, expected_v=0)
        self.assertTrue(
            st2.startswith("409") or (isinstance(data2, dict) and data2.get("error") == "conflict"),
            (st2, data2),
        )
        self.assertNotIn("fop_newx", self._ids())
        a = next(o for o in self.store["payload"]["financeRec"]["operations"] if o["finance_operation_id"] == "fop_a")
        self.assertEqual(a["amount"], 111)

    def test_bulk_atomic_with_batch_and_audit(self):
        cookie = self.login()
        items = [_op_item("fop_imp%03d" % i, import_batch_id="fimp_1", import_fingerprint="fp%03d" % i) for i in range(50)]
        st, data, *_ = self.batch(
            cookie,
            [
                {"path": wt_api.FR_OPS_BULK_PATH, "expected_v": 0, "value": items},
                {
                    "path": "financeRec.import_batches.fimp_1",
                    "expected_v": 0,
                    "value": {"import_batch_id": "fimp_1", "batch_key": "k1", "rows_added": 50},
                },
                {
                    "path": "financeRec.audit.faud_imp",
                    "expected_v": 0,
                    "value": {
                        "audit_id": "faud_imp",
                        "kind": "expense_import",
                        "import_batch_id": "fimp_1",
                    },
                },
            ],
            atomic=True,
        )
        self.assertTrue(st.startswith("200"), (st, data))
        self.assertIn("fop_imp000", self._ids())
        self.assertIn("fimp_1", self._bids())
        self.assertIn("fimp_old", self._bids())
        self.assertIn("faud_imp", self._aids())
        self.assertIn("faud_old", self._aids())

    def test_bulk_mid_conflict_rolls_back_batch_meta(self):
        cookie = self.login()
        items = [_op_item("fop_r%03d" % i) for i in range(10)]
        # audit path conflict: reuse existing faud_old as create → immutable
        st, data, *_ = self.batch(
            cookie,
            [
                {"path": wt_api.FR_OPS_BULK_PATH, "expected_v": 0, "value": items},
                {
                    "path": "financeRec.audit.faud_old",
                    "expected_v": 0,
                    "value": {"audit_id": "faud_old", "kind": "hack"},
                },
            ],
            atomic=True,
        )
        self.assertTrue(
            st.startswith("409")
            or st.startswith("400")
            or (isinstance(data, dict) and data.get("error") in ("immutable", "conflict")),
            (st, data),
        )
        self.assertNotIn("fop_r000", self._ids())
        old = next(a for a in self.store["payload"]["financeRec"]["audit"] if a["audit_id"] == "faud_old")
        self.assertEqual(old.get("kind"), "seed")

    def test_audit_create_by_id_keeps_history(self):
        cookie = self.login()
        st, data, *_ = self.op(
            cookie,
            "financeRec.audit.faud_new",
            {"audit_id": "faud_new", "kind": "op_add", "new_value": 10},
            expected_v=0,
        )
        self.assertTrue(st.startswith("200"), (st, data))
        self.assertEqual(self._aids(), ["faud_old", "faud_new"])

    def test_audit_immutable_update_and_null(self):
        cookie = self.login()
        st1, d1, *_ = self.op(
            cookie,
            "financeRec.audit.faud_old",
            {"audit_id": "faud_old", "kind": "changed"},
            expected_v=0,
        )
        self.assertTrue(
            (isinstance(d1, dict) and d1.get("error") == "immutable") or st1.startswith("400"),
            (st1, d1),
        )
        st2, d2, *_ = self.op(cookie, "financeRec.audit.faud_old", None, expected_v=0)
        self.assertTrue(
            (isinstance(d2, dict) and d2.get("error") == "immutable") or st2.startswith("400"),
            (st2, d2),
        )
        self.assertEqual(
            next(a for a in self.store["payload"]["financeRec"]["audit"] if a["audit_id"] == "faud_old")["kind"],
            "seed",
        )

    def test_audit_survives_reload_meta(self):
        cookie = self.login()
        st, *_ = self.op(
            cookie,
            "financeRec.audit.faud_keep",
            {"audit_id": "faud_keep", "kind": "op_edit"},
            expected_v=0,
        )
        self.assertTrue(st.startswith("200"), st)
        # Имитация повторного чтения: запись в payload, meta на месте.
        self.assertIn("faud_keep", self._aids())
        self.assertGreaterEqual(
            wt_api.record_version(self.store["payload"], "financeRec.audit.faud_keep"),
            1,
        )

    def test_import_batch_by_id_keeps_peer(self):
        cookie = self.login()
        st, data, *_ = self.op(
            cookie,
            "financeRec.import_batches.fimp_new",
            {"import_batch_id": "fimp_new", "batch_key": "newk", "rows_added": 3},
            expected_v=0,
        )
        self.assertTrue(st.startswith("200"), (st, data))
        self.assertIn("fimp_old", self._bids())
        self.assertIn("fimp_new", self._bids())

    def test_two_parallel_import_batches(self):
        cookie = self.login()
        st1, *_ = self.batch(
            cookie,
            [
                {
                    "path": wt_api.FR_OPS_BULK_PATH,
                    "expected_v": 0,
                    "value": [_op_item("fop_p1", import_batch_id="fimp_p1")],
                },
                {
                    "path": "financeRec.import_batches.fimp_p1",
                    "expected_v": 0,
                    "value": {"import_batch_id": "fimp_p1", "batch_key": "p1"},
                },
            ],
        )
        self.assertTrue(st1.startswith("200"), st1)
        ev_bulk = wt_api.record_version(self.store["payload"], wt_api.FR_OPS_BULK_PATH)
        st2, *_ = self.batch(
            cookie,
            [
                {
                    "path": wt_api.FR_OPS_BULK_PATH,
                    "expected_v": ev_bulk,
                    "value": [_op_item("fop_p2", import_batch_id="fimp_p2")],
                },
                {
                    "path": "financeRec.import_batches.fimp_p2",
                    "expected_v": 0,
                    "value": {"import_batch_id": "fimp_p2", "batch_key": "p2"},
                },
            ],
        )
        self.assertTrue(st2.startswith("200"), st2)
        self.assertIn("fop_p1", self._ids())
        self.assertIn("fop_p2", self._ids())
        self.assertIn("fimp_p1", self._bids())
        self.assertIn("fimp_p2", self._bids())

    def test_import_with_manual_op_concurrent(self):
        cookie = self.login()
        st_m, *_ = self.op(
            cookie,
            "financeRec.operations.fop_manual",
            {"finance_operation_id": "fop_manual", "amount": 77, "comment": "m", "is_deleted": False},
            expected_v=0,
        )
        self.assertTrue(st_m.startswith("200"), st_m)
        ev = wt_api.record_version(self.store["payload"], wt_api.FR_OPS_BULK_PATH)
        st_i, *_ = self.op(
            cookie,
            wt_api.FR_OPS_BULK_PATH,
            [_op_item("fop_imp_only", amount=5)],
            expected_v=ev,
        )
        self.assertTrue(st_i.startswith("200"), st_i)
        self.assertIn("fop_manual", self._ids())
        self.assertIn("fop_imp_only", self._ids())

    def test_forbidden_without_finance_edit(self):
        cookie = self.login("usr_001", "1111")
        st, data, *_ = self.op(
            cookie,
            "financeRec.audit.faud_x",
            {"audit_id": "faud_x", "kind": "x"},
            expected_v=0,
        )
        self.assertTrue(
            st.startswith("403") or (isinstance(data, dict) and data.get("error") == "forbidden"),
            (st, data),
        )

    def test_ops_disabled_503(self):
        os.environ["WT_OPS_ENABLED"] = "0"
        cookie = self.login()
        st, data, *_ = self.op(
            cookie,
            wt_api.FR_OPS_BULK_PATH,
            [_op_item("fop_z")],
            expected_v=0,
        )
        self.assertTrue(st.startswith("503"), (st, data))

    def test_bulk_returns_item_versions(self):
        cookie = self.login()
        st, data, *_ = self.batch(
            cookie,
            [{"path": wt_api.FR_OPS_BULK_PATH, "expected_v": 0, "value": [_op_item("fop_v1"), _op_item("fop_v2")]}],
        )
        self.assertTrue(st.startswith("200"), (st, data))
        row = data["results"][0]
        paths = {it["path"]: it["record_v"] for it in row.get("items", [])}
        self.assertEqual(paths.get("financeRec.operations.fop_v1"), 1)
        self.assertEqual(paths.get("financeRec.operations.fop_v2"), 1)
        # Виртуальный $bulk не хранит meta и не попадает в массив.
        self.assertEqual(wt_api.record_version(self.store["payload"], wt_api.FR_OPS_BULK_PATH), 0)
        self.assertNotIn("$bulk", self._ids())

    def test_bulk_1000_mid_invalid_item_rolls_back_all(self):
        """Отказ сервера посреди большого импорта: битый элемент №500 → ничего не записано."""
        cookie = self.login()
        items = [_op_item("fop_m%04d" % i) for i in range(1000)]
        items[500] = {"expected_v": 0, "value": {"amount": 1}}  # без id
        st, data, *_ = self.batch(
            cookie,
            [
                {"path": wt_api.FR_OPS_BULK_PATH, "expected_v": 0, "value": items},
                {
                    "path": "financeRec.import_batches.fimp_m",
                    "expected_v": 0,
                    "value": {"import_batch_id": "fimp_m", "batch_key": "m"},
                },
            ],
        )
        self.assertFalse(st.startswith("200"), (st, data))
        self.assertNotIn("fop_m0000", self._ids())
        self.assertNotIn("fimp_m", self._bids())

    def test_bulk_second_import_same_file_no_duplicates(self):
        """Повторный импорт одного файла: upsert по id, не дубль."""
        cookie = self.login()
        items = [_op_item("fop_s%02d" % i, import_fingerprint="fp_s%02d" % i) for i in range(5)]
        st1, *_ = self.op(cookie, wt_api.FR_OPS_BULK_PATH, items, expected_v=0)
        self.assertTrue(st1.startswith("200"), st1)
        items2 = [_op_item("fop_s%02d" % i, expected_v=1, import_fingerprint="fp_s%02d" % i) for i in range(5)]
        st2, *_ = self.op(cookie, wt_api.FR_OPS_BULK_PATH, items2, expected_v=0)
        self.assertTrue(st2.startswith("200"), st2)
        self.assertEqual(len([x for x in self._ids() if str(x).startswith("fop_s")]), 5)

    def test_child_write_bumps_root_stale_whole_array_gets_409(self):
        """Запись по id поднимает корень: широкий писатель со старой версией — 409, не wipe."""
        cookie = self.login()
        ev_root = wt_api.record_version(self.store["payload"], "financeRec.audit")
        st1, *_ = self.op(
            cookie,
            "financeRec.audit.faud_child",
            {"audit_id": "faud_child", "kind": "op_add"},
            expected_v=0,
        )
        self.assertTrue(st1.startswith("200"), st1)
        self.assertGreater(wt_api.record_version(self.store["payload"], "financeRec.audit"), ev_root)
        st2, data2, *_ = self.op(
            cookie,
            "financeRec.audit",
            [{"audit_id": "faud_old", "kind": "seed"}, {"audit_id": "faud_stale", "kind": "x"}],
            expected_v=ev_root,
        )
        self.assertTrue(st2.startswith("409"), (st2, data2))
        self.assertIn("faud_child", self._aids())
        # Операции: то же правило.
        ev_ops = wt_api.record_version(self.store["payload"], "financeRec.operations")
        st3, *_ = self.op(
            cookie,
            "financeRec.operations.fop_child",
            {"finance_operation_id": "fop_child", "amount": 1, "is_deleted": False},
            expected_v=0,
        )
        self.assertTrue(st3.startswith("200"), st3)
        st4, *_ = self.op(
            cookie,
            "financeRec.operations",
            [{"finance_operation_id": "fop_a", "amount": 100, "is_deleted": False}],
            expected_v=ev_ops,
        )
        self.assertTrue(st4.startswith("409"), st4)
        self.assertIn("fop_child", self._ids())
        self.assertIn("fop_b", self._ids())

    def test_root_write_bumps_changed_children(self):
        """Широкая запись корня поднимает версии изменённых id: старый by-id писатель — 409."""
        cookie = self.login()
        ev = wt_api.record_version(self.store["payload"], "financeRec.operations")
        st1, *_ = self.op(
            cookie,
            "financeRec.operations",
            [
                {"finance_operation_id": "fop_a", "amount": 555, "is_deleted": False},
                {"finance_operation_id": "fop_b", "amount": 200, "is_deleted": False},
            ],
            expected_v=ev,
        )
        self.assertTrue(st1.startswith("200"), st1)
        self.assertEqual(wt_api.record_version(self.store["payload"], "financeRec.operations.fop_a"), 1)
        self.assertEqual(wt_api.record_version(self.store["payload"], "financeRec.operations.fop_b"), 0)
        st2, *_ = self.op(
            cookie,
            "financeRec.operations.fop_a",
            {"finance_operation_id": "fop_a", "amount": 1, "is_deleted": False},
            expected_v=0,
        )
        self.assertTrue(st2.startswith("409"), st2)

    def test_whole_audit_append_only(self):
        cookie = self.login()
        ev = wt_api.record_version(self.store["payload"], "financeRec.audit")
        # Удаление истории — отказ.
        st1, d1, *_ = self.op(cookie, "financeRec.audit", [], expected_v=ev)
        self.assertFalse(st1.startswith("200"), (st1, d1))
        self.assertEqual(d1.get("error"), "immutable")
        # Правка истории — отказ.
        st2, d2, *_ = self.op(
            cookie, "financeRec.audit", [{"audit_id": "faud_old", "kind": "edited"}], expected_v=ev
        )
        self.assertEqual(d2.get("error"), "immutable", (st2, d2))
        # Дополнение — можно.
        st3, *_ = self.op(
            cookie,
            "financeRec.audit",
            [{"audit_id": "faud_old", "kind": "seed"}, {"audit_id": "faud_app", "kind": "close"}],
            expected_v=ev,
        )
        self.assertTrue(st3.startswith("200"), st3)
        self.assertEqual(self._aids(), ["faud_old", "faud_app"])

    def test_audit_identical_repeat_idempotent_no_dup(self):
        cookie = self.login()
        body = {"audit_id": "faud_rep", "kind": "op_add", "new_value": 5}
        st1, *_ = self.op(cookie, "financeRec.audit.faud_rep", body, expected_v=0)
        self.assertTrue(st1.startswith("200"), st1)
        v = wt_api.record_version(self.store["payload"], "financeRec.audit.faud_rep")
        st2, *_ = self.op(cookie, "financeRec.audit.faud_rep", body, expected_v=v)
        self.assertTrue(st2.startswith("200"), st2)
        self.assertEqual(self._aids().count("faud_rep"), 1)
        st3, d3, *_ = self.op(
            cookie, "financeRec.audit.faud_rep", dict(body, new_value=6), expected_v=v
        )
        self.assertEqual(d3.get("error"), "immutable", (st3, d3))

    def test_manual_op_and_audit_one_atomic_batch_survive_reload(self):
        cookie = self.login()
        st, data, *_ = self.batch(
            cookie,
            [
                {
                    "path": "financeRec.operations.fop_man",
                    "expected_v": 0,
                    "value": {"finance_operation_id": "fop_man", "amount": 9, "is_deleted": False},
                },
                {
                    "path": "financeRec.audit.faud_man",
                    "expected_v": 0,
                    "value": {"audit_id": "faud_man", "kind": "op_add", "finance_operation_id": "fop_man"},
                },
            ],
        )
        self.assertTrue(st.startswith("200"), (st, data))
        st_g, state, *_ = call("GET", "/api/state", cookie=cookie)
        self.assertTrue(st_g.startswith("200"), st_g)
        fr = state["payload"]["financeRec"]
        self.assertIn("fop_man", [o["finance_operation_id"] for o in fr["operations"]])
        self.assertIn("faud_man", [a["audit_id"] for a in fr["audit"]])
        self.assertIn("faud_old", [a["audit_id"] for a in fr["audit"]])

    def test_manual_op_audit_conflict_rolls_back_both(self):
        cookie = self.login()
        st, data, *_ = self.batch(
            cookie,
            [
                {
                    "path": "financeRec.operations.fop_x2",
                    "expected_v": 0,
                    "value": {"finance_operation_id": "fop_x2", "amount": 9, "is_deleted": False},
                },
                {
                    "path": "financeRec.audit.faud_old",
                    "expected_v": 0,
                    "value": {"audit_id": "faud_old", "kind": "dup"},
                },
            ],
        )
        self.assertFalse(st.startswith("200"), (st, data))
        self.assertNotIn("fop_x2", self._ids())

    def test_put_audit_change_bumps_root(self):
        """PUT, добавивший запись журнала, поднимает и корень списка."""
        before = json.loads(json.dumps(self.store["payload"]))
        after = json.loads(json.dumps(before))
        after["financeRec"]["audit"].append({"audit_id": "faud_put", "kind": "x"})
        out = wt_api.sync_record_versions_after_put(before, after, {"user_id": "user_admin"})
        self.assertGreaterEqual(wt_api.record_version(out, "financeRec.audit.faud_put"), 1)
        self.assertGreaterEqual(wt_api.record_version(out, "financeRec.audit"), 1)

    def test_regression_users_employees_vacation(self):
        cookie = self.login()
        st_u, *_ = self.op(
            cookie,
            "users.user_admin.name",
            "Admin Bulk",
            expected_v=wt_api.record_version(self.store["payload"], "users.user_admin.name"),
        )
        self.assertTrue(st_u.startswith("200"), st_u)
        st_e, *_ = self.op(
            cookie,
            "workspaces.shop.employees.e_shop.rate",
            1600,
            expected_v=wt_api.record_version(self.store["payload"], "workspaces.shop.employees.e_shop.rate"),
        )
        self.assertTrue(st_e.startswith("200"), st_e)
        st_v, *_ = self.op(
            cookie,
            "vacationPlans.2026.vp_1.status",
            "approved",
            expected_v=0,
        )
        self.assertTrue(st_v.startswith("200"), st_v)


if __name__ == "__main__":
    unittest.main()
