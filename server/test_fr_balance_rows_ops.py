# -*- coding: utf-8 -*-
"""
Этап 4Б: OPS-адресация строк financeRec.openings / day_closings / period_states
по смысловому ключу (объект + неделя/дата + счёт). FakeConn, искусственные данные, без live / Timeweb.
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

WEEK = "week:2026-11-05"
NEXT = "week:2026-11-12"


def _opening(src, value, period=NEXT, **extra):
    row = {
        "finance_area_id": "farea_shop", "object_id": "farea_shop", "period_key": period,
        "finance_source_id": src, "account_id": src, "value": value, "carried": True, "is_manual": False,
    }
    row.update(extra)
    return row


def _closing(src, date, actual, **extra):
    row = {
        "finance_day_closing_id": "fdc_" + src + "_" + date.replace("-", "").replace(".", ""),
        "finance_area_id": "farea_shop", "object_id": "farea_shop", "period_key": WEEK,
        "date": date, "finance_source_id": src, "account_id": src,
        "actual_balance": actual, "actual_closing": actual,
    }
    row.update(extra)
    return row


def _period(status, period=WEEK, **extra):
    row = {"finance_period_id": "fper_1", "finance_area_id": "farea_shop", "period_key": period, "status": status}
    row.update(extra)
    return row


def key(list_key, row):
    return wt_api.fr_row_key(list_key, row)


def path(list_key, row):
    return "financeRec.%s.%s" % (list_key, key(list_key, row))


class FrBalanceRowsOpsTests(unittest.TestCase):
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
        payload["financeRec"] = {
            "operations": [{"finance_operation_id": "fop_a", "amount": 100, "is_deleted": False}],
            "audit": [{"audit_id": "faud_old", "kind": "seed"}],
            "openings": [_opening("fsrc_cash", 500), _opening("fsrc_bank", 70)],
            "day_closings": [
                _closing("fsrc_cash", "2026-11-05", 100),
                # Старая строка с датой «д.м.г» — ключ нормализуется так же, как в клиенте.
                _closing("fsrc_bank", "06.11.2026", 40),
            ],
            "period_states": [_period("draft")],
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

    def op(self, cookie, p, value, expected_v=0):
        return call("POST", "/api/state/op", {"op": {"path": p, "expected_v": expected_v, "value": value}}, cookie=cookie)

    def batch(self, cookie, ops, atomic=True):
        return call("POST", "/api/state/op/batch", {"ops": ops, "atomic": atomic}, cookie=cookie)

    def fr(self):
        return self.store["payload"]["financeRec"]

    def meta_v(self, p):
        meta = (self.store["payload"].get("_wt_records") or {}).get(p) or {}
        return int(meta.get("v") or 0)

    def test_key_uses_existing_fields_and_encodes_segment(self):
        k = key("openings", _opening("fsrc_cash", 1))
        self.assertEqual(k, "farea-5Fshop_week-3A2026-2D11-2D12_fsrc-5Fcash")
        self.assertTrue(wt_api.normalize_op_path("financeRec.openings." + k))
        # object_id/account_id приоритетнее старых полей — ключ не меняется после frNormalizeBalanceRow.
        legacy = {"finance_area_id": "farea_shop", "period_key": NEXT, "finance_source_id": "fsrc_cash"}
        self.assertEqual(key("openings", legacy), k)
        self.assertEqual(
            key("day_closings", _closing("fsrc_bank", "06.11.2026", 1)),
            key("day_closings", _closing("fsrc_bank", "2026-11-06", 1)),
        )
        self.assertEqual(key("period_states", _period("draft")), "farea-5Fshop_week-3A2026-2D11-2D05")
        self.assertEqual(key("openings", {"finance_area_id": "farea_shop", "period_key": NEXT}), "")
        self.assertEqual(key("openings", {"object_id": "Касса №1", "period_key": NEXT, "account_id": "a b"}),
                         "-D0-9A-D0-B0-D1-81-D1-81-D0-B0-20-E2-84-961_week-3A2026-2D11-2D12_a-20b")

    def test_read_create_and_edit_by_key(self):
        cookie = self.login()
        st, data, *_ = call("GET", "/api/state", cookie=cookie)
        self.assertTrue(st.startswith("200"))
        got = wt_api.get_path_value(data["payload"], path("day_closings", _closing("fsrc_bank", "2026-11-06", 0)))
        self.assertEqual(got["actual_balance"], 40)
        new_row = _opening("fsrc_card", 15)
        st, data, *_ = self.op(cookie, path("openings", new_row), new_row, 0)
        self.assertTrue(st.startswith("200"), (st, data))
        self.assertIsInstance(self.fr()["openings"], list)
        self.assertEqual(len(self.fr()["openings"]), 3)
        v1 = self.meta_v(path("openings", new_row))
        self.assertGreater(v1, 0)
        st, data, *_ = self.op(cookie, path("openings", new_row), dict(new_row, value=25), v1)
        self.assertTrue(st.startswith("200"), (st, data))
        self.assertEqual(self.fr()["openings"][2]["value"], 25)
        self.assertEqual(len(self.fr()["openings"]), 3)
        self.assertGreater(self.meta_v(path("openings", new_row)), v1)

    def test_legacy_date_row_updated_in_place(self):
        cookie = self.login()
        row = dict(self.fr()["day_closings"][1], actual_balance=44, actual_closing=44)
        st, data, *_ = self.op(cookie, path("day_closings", row), row, 0)
        self.assertTrue(st.startswith("200"), (st, data))
        self.assertEqual(len(self.fr()["day_closings"]), 2)
        self.assertEqual(self.fr()["day_closings"][1]["actual_balance"], 44)
        self.assertEqual(self.fr()["day_closings"][1]["date"], "06.11.2026")

    def test_legacy_period_state_object_only_not_addressed(self):
        # Ключ статуса — только finance_area_id (как frPeriodState): старая строка лишь с object_id
        # не совпадает с рабочей строкой недели и не изменяется.
        legacy = {"object_id": "farea_shop", "period_key": NEXT, "status": "closed", "closed_by": "old"}
        self.fr()["period_states"].append(dict(legacy))
        self.assertEqual(key("period_states", legacy), "")
        cookie = self.login()
        row = _period("filled", period=NEXT, finance_period_id="fper_2")
        st, data, *_ = self.op(cookie, path("period_states", row), row, 0)
        self.assertTrue(st.startswith("200"), (st, data))
        states = self.fr()["period_states"]
        self.assertEqual(states[1], legacy)
        self.assertEqual([s for s in states if s.get("finance_area_id") == "farea_shop" and s["period_key"] == NEXT], [row])
        self.assertEqual(len(states), 3)
        # Тело лишь с object_id нельзя записать по ключу рабочей строки.
        st, data, *_ = self.op(cookie, path("period_states", row), dict(legacy, status="review"), self.meta_v(path("period_states", row)))
        self.assertTrue(st.startswith("400"), (st, data))
        self.assertEqual(self.fr()["period_states"][1], legacy)

    def test_two_employees_different_rows_both_saved(self):
        a = self.login()
        b = self.login("usr_dir", "4444")
        cash = dict(self.fr()["openings"][0], value=510, is_manual=True, carried=False)
        bank = dict(self.fr()["openings"][1], value=80, is_manual=True, carried=False)
        st, data, *_ = self.op(a, path("openings", cash), cash, 0)
        self.assertTrue(st.startswith("200"), (st, data))
        st, data, *_ = self.op(b, path("openings", bank), bank, 0)
        self.assertTrue(st.startswith("200"), (st, data))
        self.assertEqual([o["value"] for o in self.fr()["openings"]], [510, 80])
        self.assertEqual(len(self.fr()["day_closings"]), 2)
        self.assertEqual(self.fr()["period_states"], [_period("draft")])

    def test_same_row_stale_version_is_conflict(self):
        a = self.login()
        b = self.login("usr_dir", "4444")
        base = json.loads(json.dumps(self.fr()["openings"][0]))
        st, *_ = self.op(b, path("openings", base), dict(base, value=900, is_manual=True), 0)
        self.assertTrue(st.startswith("200"), st)
        st, data, *_ = self.op(a, path("openings", base), dict(base, value=111), 0)
        self.assertTrue(st.startswith("409"), (st, data))
        self.assertEqual(data.get("path"), path("openings", base))
        self.assertEqual(data["server_value"]["value"], 900)
        self.assertEqual(self.fr()["openings"][0]["value"], 900)

    def test_create_existing_key_with_zero_version_conflicts_after_write(self):
        cookie = self.login()
        row = _period("draft", period=NEXT, finance_period_id="fper_2")
        st, *_ = self.op(cookie, path("period_states", row), row, 0)
        self.assertTrue(st.startswith("200"))
        st, data, *_ = self.op(cookie, path("period_states", row), dict(row, finance_period_id="fper_3", status="filled"), 0)
        self.assertTrue(st.startswith("409"), (st, data))
        rows = [p for p in self.fr()["period_states"] if p["period_key"] == NEXT]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "draft")

    def test_invalid_writes_rejected(self):
        cookie = self.login()
        row = self.fr()["openings"][0]
        p = path("openings", row)
        before = json.loads(json.dumps(self.fr()))
        cases = [
            (p, None, "null"),
            (p, "text", "не объект"),
            (p, dict(row, finance_source_id="fsrc_other", account_id="fsrc_other"), "ключ тела ≠ путь"),
            (p + ".value", 1, "полевая правка"),
            ("financeRec.openings.nosuchkey", _opening("fsrc_cash", 1), "ключ пути ≠ тело"),
        ]
        for case_path, value, label in cases:
            st, data, *_ = self.op(cookie, case_path, value, self.meta_v(case_path))
            self.assertFalse(st.startswith("200"), (label, st, data))
        self.assertEqual(self.fr(), before)

    def test_child_write_bumps_root_and_stale_wide_write_conflicts(self):
        cookie = self.login()
        root_v = self.meta_v("financeRec.openings")
        stale_list = json.loads(json.dumps(self.fr()["openings"]))
        row = dict(self.fr()["openings"][1], value=99)
        st, *_ = self.op(cookie, path("openings", row), row, 0)
        self.assertTrue(st.startswith("200"))
        self.assertGreater(self.meta_v("financeRec.openings"), root_v)
        st, data, *_ = self.op(cookie, "financeRec.openings", stale_list, root_v)
        self.assertTrue(st.startswith("409"), (st, data))
        self.assertEqual(self.fr()["openings"][1]["value"], 99)

    def test_root_write_bumps_changed_rows_so_stale_row_write_conflicts(self):
        cookie = self.login()
        cash = json.loads(json.dumps(self.fr()["openings"][0]))
        new_list = json.loads(json.dumps(self.fr()["openings"]))
        new_list[0]["value"] = 777
        st, *_ = self.op(cookie, "financeRec.openings", new_list, self.meta_v("financeRec.openings"))
        self.assertTrue(st.startswith("200"))
        self.assertGreater(self.meta_v(path("openings", cash)), 0)
        self.assertEqual(self.meta_v(path("openings", self.fr()["openings"][1])), 0)
        st, data, *_ = self.op(cookie, path("openings", cash), dict(cash, value=1), 0)
        self.assertTrue(st.startswith("409"), (st, data))
        self.assertEqual(self.fr()["openings"][0]["value"], 777)

    def test_put_bumps_changed_row_versions(self):
        cookie = self.login()
        st, data, *_ = call("GET", "/api/state", cookie=cookie)
        body = json.loads(json.dumps(data["payload"]))
        body["financeRec"]["day_closings"][0]["actual_balance"] = 123
        st, data, *_ = call("PUT", "/api/state", {"version": data["version"], "payload": body}, cookie=cookie)
        self.assertTrue(st.startswith("200"), (st, data))
        row = self.fr()["day_closings"][0]
        self.assertEqual(row["actual_balance"], 123)
        self.assertGreater(self.meta_v(path("day_closings", row)), 0)
        self.assertEqual(self.meta_v(path("day_closings", self.fr()["day_closings"][1])), 0)
        st, data, *_ = self.op(cookie, path("day_closings", row), dict(row, actual_balance=5), 0)
        self.assertTrue(st.startswith("409"), (st, data))

    def test_atomic_batch_rows_and_audit_all_or_nothing(self):
        cookie = self.login()
        closing = dict(self.fr()["day_closings"][0], date="2026-11-11", actual_balance=300, actual_closing=300)
        carry = dict(self.fr()["openings"][0], value=300)
        good = [
            {"path": path("day_closings", closing), "expected_v": 0, "value": closing},
            {"path": path("openings", carry), "expected_v": 0, "value": carry},
            {"path": "financeRec.audit.faud_day", "expected_v": 0, "value": {"audit_id": "faud_day", "kind": "day_actual"}},
        ]
        # Строку переноса уже изменил другой сотрудник → 2-я часть пакета устарела.
        other = dict(self.fr()["openings"][0], value=505, is_manual=True)
        st, *_ = self.op(self.login("usr_dir", "4444"), path("openings", other), other, 0)
        self.assertTrue(st.startswith("200"))
        before = json.loads(json.dumps(self.fr()))
        st, data, *_ = self.batch(cookie, good)
        self.assertTrue(st.startswith("409"), (st, data))
        self.assertEqual(self.fr(), before)
        good[1]["expected_v"] = self.meta_v(path("openings", carry))
        st, data, *_ = self.batch(cookie, good)
        self.assertTrue(st.startswith("200"), (st, data))
        self.assertEqual(len(self.fr()["day_closings"]), 3)
        self.assertEqual(self.fr()["openings"][0]["value"], 300)
        self.assertIn("faud_day", [a.get("audit_id") for a in self.fr()["audit"]])

    def test_non_atomic_batch_returns_server_value_for_conflicting_row(self):
        cookie = self.login()
        other = dict(self.fr()["openings"][0], value=505, is_manual=True)
        st, *_ = self.op(self.login("usr_dir", "4444"), path("openings", other), other, 0)
        self.assertTrue(st.startswith("200"))
        mine_cash = dict(self.fr()["openings"][0], value=1, is_manual=False)
        mine_bank = dict(self.fr()["openings"][1], value=2)
        st, data, *_ = self.batch(cookie, [
            {"path": path("openings", mine_cash), "expected_v": 0, "value": mine_cash},
            {"path": path("openings", mine_bank), "expected_v": 0, "value": mine_bank},
        ], atomic=False)
        self.assertTrue(st.startswith("200"), (st, data))
        by_path = {r["path"]: r for r in data["results"]}
        self.assertEqual(by_path[path("openings", mine_cash)]["status"], "conflict")
        self.assertEqual(by_path[path("openings", mine_cash)]["server_value"]["value"], 505)
        self.assertEqual(by_path[path("openings", mine_bank)]["status"], "saved")
        self.assertEqual([o["value"] for o in self.fr()["openings"]], [505, 2])

    def test_user_without_finance_edit_forbidden(self):
        cookie = self.login("usr_001", "1111")
        row = dict(self.fr()["openings"][0], value=1)
        st, data, *_ = self.op(cookie, path("openings", row), row, 0)
        self.assertTrue(st.startswith("403"), (st, data))
        self.assertEqual(self.fr()["openings"][0]["value"], 500)


if __name__ == "__main__":
    unittest.main()
