# -*- coding: utf-8 -*-
"""
Этап 4Б Финконтроля: остатки, фактические остатки, статусы и перенос — построчно по ключу.
Локальный wt_api на 127.0.0.1 (FakeConn, искусственные данные) + настоящие fr-mod.js (целиком)
и DomainWriter из app.html в Node. Без live / Timeweb.
"""

from __future__ import annotations

import json
import os
import unittest

from test_fr_close_period_atomic import NODE
from test_fr_stage4_audit import _FrStage4Harness

HERE = os.path.dirname(os.path.abspath(__file__))
ROWS_HARNESS = os.path.join(HERE, "fr_stage4b_rows_harness.js")
AREA = "farea_shop"
WEEK = "week:2026-11-05"
NEXT = "week:2026-11-12"
ROW_ROOTS = ("financeRec.openings", "financeRec.day_closings", "financeRec.period_states")
WIDE = ROW_ROOTS + ("financeRec.audit", "financeRec.operations", "financeRec")


def balance_seed():
    def src(sid, name, typ, order, **extra):
        row = {"finance_source_id": sid, "account_id": sid, "finance_area_id": AREA, "object_id": AREA,
               "name": name, "account_type": typ, "is_active": True, "sort_order": order}
        row.update(extra)
        return row

    def op(oid, sid, date, kind, amount):
        return {"finance_operation_id": oid, "object_id": AREA, "finance_area_id": AREA, "account_id": sid,
                "finance_source_id": sid, "date": date, "type": "in" if kind == "income" else "out",
                "op_type": kind, "movement_kind": kind, "amount": amount, "is_deleted": False}

    def opening(sid, value):
        return {"finance_area_id": AREA, "object_id": AREA, "period_key": "week:2026-10-29",
                "finance_source_id": sid, "account_id": sid, "value": value, "is_manual": True}

    return {
        "areas": [{"finance_area_id": AREA, "object_id": AREA, "name": "Магазин Володарского", "sort_order": 10,
                   "is_active": True, "primary_cash_source_id": "fsrc_cash"}],
        "sources": [
            src("fsrc_cash", "Основная касса", "cash", 1),
            src("fsrc_cash2", "Касса 2", "cash", 2, in_actual_balance=False),
            src("fsrc_bank", "Р/с", "bank", 3),
        ],
        "categories": [],
        "operations": [
            op("fop_seed1", "fsrc_cash", "2026-11-05", "income", 5000),
            op("fop_seed2", "fsrc_bank", "2026-11-06", "expense", 300),
            op("fop_prev1", "fsrc_cash", "2026-10-30", "income", 1200),
        ],
        "money_transits": [],
        "openings": [opening("fsrc_cash", 1000), opening("fsrc_bank", 2000)],
        "day_closings": [],
        "period_states": [],
        "formulas": [],
        "audit": [{"audit_id": "faud_old", "kind": "seed"}],
        "import_batches": [],
    }


@unittest.skipUnless(NODE, "node не найден (WT_NODE_BIN)")
class FrStage4BRowsTests(_FrStage4Harness):
    def setUp(self):
        super().setUp()
        self.store["payload"]["financeRec"] = balance_seed()

    def run_rows(self, name):
        return self.run_scenario(name, harness=ROWS_HARNESS)

    def rows(self, list_key, **match):
        return [r for r in self.stored_fr()[list_key] if all(r.get(k) == v for k, v in match.items())]

    def atomic_batches(self, out):
        return [b for b in out["log"]["batches"] if b["atomic"]]

    def assert_no_wide(self, out):
        for b in out["log"]["batches"]:
            for p in b["paths"]:
                self.assertNotIn(p, WIDE, b)
        for p in out["collected"]:
            self.assertNotIn(p, ROW_ROOTS, out["collected"])
        self.assertEqual(out["log"]["singleOps"], [], out["log"])
        self.assertEqual(out["log"]["puts"], 0)

    def assert_no_success(self, out):
        self.assertNotIn("success", [t["kind"] for t in out["log"]["toasts"]])
        self.assertTrue(any(t["kind"] == "error" for t in out["log"]["toasts"]), out["log"]["toasts"])

    # 1. Два сотрудника сохраняют разные остатки.
    def test_day_actual_two_employees_different_rows(self):
        out = self.run_rows("day_actual_two_employees")
        self.assertEqual(out["other_status"], 200)
        self.assert_no_wide(out)
        first = self.atomic_batches(out)[0]
        self.assertEqual(first["status"], 200, first)
        self.assertEqual(len([p for p in first["paths"] if p.startswith("financeRec.day_closings.")]), 1, first)
        self.assertEqual(len([p for p in first["paths"] if p.startswith("financeRec.audit.")]), 1, first)
        self.assertEqual(len(first["paths"]), 2, first)
        self.assertEqual(self.rows("day_closings", account_id="fsrc_bank")[0]["actual_balance"], 900)
        self.assertEqual(self.rows("day_closings", account_id="fsrc_cash")[0]["actual_balance"], 5800)
        self.assertIn("day_actual", [a.get("kind") for a in self.stored_fr()["audit"]])

    def test_opening_two_employees_different_rows(self):
        out = self.run_rows("opening_two_employees")
        self.assertEqual(out["other_status"], 200)
        self.assert_no_wide(out)
        self.assertEqual(self.atomic_batches(out)[0]["status"], 200)
        cash = self.rows("openings", period_key=WEEK, account_id="fsrc_cash")
        bank = self.rows("openings", period_key=WEEK, account_id="fsrc_bank")
        self.assertEqual(len(cash), 1)
        self.assertEqual((cash[0]["value"], cash[0]["is_manual"]), (1500, True))
        # Перенос, посчитанный отрисовкой у А, не затёр ручной остаток Б: конфликт → принят сервер.
        self.assertEqual(len(bank), 1)
        self.assertEqual((bank[0]["value"], bank[0]["is_manual"]), (900, True))
        local_bank = [r for r in out["local"]["openings"] if r["period_key"] == WEEK and r["account_id"] == "fsrc_bank"]
        self.assertEqual(local_bank[0]["value"], 900)
        self.assertIn("opening_edit", [a.get("kind") for a in self.stored_fr()["audit"]])

    # 2. Конфликт при изменении одной записи.
    def test_opening_same_row_conflict_no_overwrite(self):
        out = self.run_rows("opening_same_row_conflict")
        self.assertEqual(out["other_status"], 200)
        self.assert_no_wide(out)
        self.assertEqual(self.atomic_batches(out)[0]["status"], 409)
        cash = self.rows("openings", period_key=WEEK, account_id="fsrc_cash")
        self.assertEqual((len(cash), cash[0]["value"]), (1, 900))
        self.assertNotIn("opening_edit", [a.get("kind") for a in self.stored_fr()["audit"]])
        self.assertNotIn("opening_edit", [a.get("kind") for a in out["local"]["audit"]])
        local_cash = [r for r in out["local"]["openings"] if r["period_key"] == WEEK and r["account_id"] == "fsrc_cash"]
        self.assertFalse(local_cash[0].get("is_manual"), local_cash)
        self.assert_no_success(out)
        self.assertTrue(any("Конфликт" in t["msg"] for t in out["log"]["toasts"]))

    # 3, 4. Сбой сети и ошибка сервера: откат, без ложного успеха.
    def _assert_day_actual_rolled_back(self, out):
        self.assert_no_wide(out)
        self.assertEqual(self.rows("day_closings"), [])
        self.assertEqual([a["audit_id"] for a in self.stored_fr()["audit"]], ["faud_old"])
        self.assertEqual(out["local_after"]["day_closings"], out["local_before"]["day_closings"])
        self.assertEqual(out["local_after"]["audit"], out["local_before"]["audit"])
        self.assert_no_success(out)

    def test_day_actual_network_error_rolls_back(self):
        out = self.run_rows("day_actual_network")
        self.assertEqual(out["log"]["batches"][0]["status"], -1)
        self._assert_day_actual_rolled_back(out)

    def test_day_actual_server_500_rolls_back(self):
        out = self.run_rows("day_actual_500")
        self.assertEqual(out["log"]["batches"][0]["status"], 500)
        self._assert_day_actual_rolled_back(out)

    # 5. Атомарность: факт на конец недели + перенос в следующую неделю + журнал.
    def test_week_actual_and_carry_in_one_atomic_batch(self):
        out = self.run_rows("week_actual_ok")
        self.assert_no_wide(out)
        first = self.atomic_batches(out)[0]
        self.assertEqual(first["status"], 200, first)
        self.assertEqual(len([p for p in first["paths"] if p.startswith("financeRec.day_closings.")]), 1, first)
        self.assertTrue([p for p in first["paths"] if p.startswith("financeRec.openings.")], first)
        self.assertEqual(len([p for p in first["paths"] if p.startswith("financeRec.audit.")]), 1, first)
        self.assertEqual(self.rows("day_closings", account_id="fsrc_cash")[0]["actual_balance"], 6000)
        nxt = self.rows("openings", period_key=NEXT, account_id="fsrc_cash")
        self.assertEqual((nxt[0]["value"], nxt[0]["carried"]), (6000, True))
        self.assertEqual(out["calc"][NEXT]["cashOpening"], 6000)

    def _assert_week_actual_untouched(self, out):
        self.assertEqual(self.atomic_batches(out)[0]["status"], 409)
        self.assertEqual(self.rows("day_closings"), [])
        self.assertEqual(self.rows("openings", period_key=NEXT), [])
        self.assertEqual([a["audit_id"] for a in self.stored_fr()["audit"]], ["faud_old"])
        self.assertEqual(out["local_after"]["day_closings"], out["local_before"]["day_closings"])
        self.assertEqual(out["local_after"]["audit"], out["local_before"]["audit"])
        before_next = [r for r in out["local_before"]["openings"] if r["period_key"] == NEXT]
        after_next = [r for r in out["local_after"]["openings"] if r["period_key"] == NEXT]
        self.assertEqual(after_next, before_next)
        self.assert_no_success(out)

    def test_week_actual_second_part_rejected_nothing_saved(self):
        self._assert_week_actual_untouched(self.run_rows("week_actual_fail_op2"))

    def test_week_actual_last_part_rejected_nothing_saved(self):
        self._assert_week_actual_untouched(self.run_rows("week_actual_fail_last"))

    # Статусы, закрытие и открытие периода — строка статуса + журнал согласованно.
    def test_status_close_reopen_consistent_with_audit(self):
        out = self.run_rows("status_close_reopen")
        self.assert_no_wide(out)
        atomic = self.atomic_batches(out)
        self.assertEqual([b["status"] for b in atomic], [200, 200, 200], atomic)
        closed = [p for p in out["after_close"]["period_states"] if p["period_key"] == WEEK]
        self.assertEqual((len(closed), closed[0]["status"]), (1, "closed"))
        self.assertTrue([o for o in out["after_close"]["openings"] if o["period_key"] == NEXT])
        kinds = [a.get("kind") for a in out["after_close"]["audit"]]
        self.assertIn("status", kinds)
        self.assertIn("close", kinds)
        st = self.rows("period_states", period_key=WEEK)
        self.assertEqual((len(st), st[0]["status"]), (1, "review"))
        self.assertIn("reopen", [a.get("kind") for a in self.stored_fr()["audit"]])
        self.assertIn("Период закрыт.", [t["msg"] for t in out["log"]["toasts"] if t["kind"] == "success"])

    def test_status_conflict_keeps_other_employee_close(self):
        out = self.run_rows("status_conflict")
        self.assertEqual(out["other_status"], 200)
        self.assertEqual(self.atomic_batches(out)[0]["status"], 409)
        st = self.rows("period_states", period_key=WEEK)
        self.assertEqual((len(st), st[0]["status"]), (1, "closed"))
        self.assertNotIn("status", [a.get("kind") for a in self.stored_fr()["audit"]])
        local = [p for p in out["local"]["period_states"] if p["period_key"] == WEEK]
        self.assertNotEqual(local[0]["status"], "review")
        self.assert_no_success(out)

    # Ручной перенос.
    def test_push_carry_saved_by_row_key(self):
        out = self.run_rows("push_carry_ok")
        self.assert_no_wide(out)
        first = self.atomic_batches(out)[0]
        self.assertEqual(first["status"], 200, first)
        self.assertTrue(all(p.startswith("financeRec.openings.") for p in first["paths"]), first)
        self.assertEqual(len(self.rows("openings", period_key=NEXT)), 3)
        self.assertIn("success", [t["kind"] for t in out["log"]["toasts"]])

    def test_push_carry_conflict_does_not_overwrite_manual_row(self):
        out = self.run_rows("push_carry_conflict")
        self.assertEqual(out["other_status"], 200)
        self.assertEqual(self.atomic_batches(out)[0]["status"], 409)
        nxt = self.rows("openings", period_key=NEXT)
        self.assertEqual([(r["account_id"], r["value"], r["is_manual"]) for r in nxt], [("fsrc_bank", 4321, True)])
        self.assert_no_success(out)

    # Побочные строки отрисовки: только отличающиеся строки, по ключу; чужие строки целы.
    def test_render_side_effects_flushed_without_overwrite(self):
        out = self.run_rows("render_flush_keeps_other_rows")
        self.assertEqual(out["other_status"], 200)
        self.assertEqual(out["collected_before"], [], out["collected_before"])
        self.assert_no_wide(out)
        flush = [b for b in out["log"]["batches"] if not b["atomic"]]
        self.assertTrue(flush, out["log"]["batches"])
        bank = self.rows("openings", period_key=WEEK, account_id="fsrc_bank")
        self.assertEqual((len(bank), bank[0]["value"], bank[0]["is_manual"]), (1, 900, True))
        cash = self.rows("openings", period_key=WEEK, account_id="fsrc_cash")
        self.assertEqual((len(cash), cash[0]["carried"]), (1, True))
        self.assertEqual(len(self.rows("period_states", period_key=WEEK)), 1)
        local_bank = [r for r in out["local"]["openings"] if r["period_key"] == WEEK and r["account_id"] == "fsrc_bank"]
        self.assertEqual(local_bank[0]["value"], 900)
        self.assertNotIn("success", [t["kind"] for t in out["log"]["toasts"]])

    def test_double_click_saves_once(self):
        out = self.run_rows("double_click")
        self.assertEqual(len(self.atomic_batches(out)), 1, out["log"]["batches"])
        self.assertTrue(any("ещё выполняется" in t["msg"] for t in out["log"]["toasts"]))
        self.assertEqual([r["account_id"] for r in self.rows("day_closings")], ["fsrc_cash"])

    def test_rollback_keeps_newer_local_edit(self):
        out = self.run_rows("rollback_keeps_newer")
        rows = [r for r in out["local"]["day_closings"] if r["date"] == "2026-11-06"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["comment"], "правка во время сохранения")
        self.assertEqual(self.rows("day_closings"), [])
        self.assert_no_success(out)


LEGACY_STATE = {"object_id": AREA, "period_key": WEEK, "status": "closed", "closed_by": "old_user"}
LEGACY_TRANSIT = {"object_id": AREA, "from_account_id": "fsrc_cash", "transit_account_id": "fsrc_transit",
                  "amount_sent": 100, "amount_closed": 0, "send_date": "2026-11-05", "comment": "старый"}


def legacy_seed():
    fr = balance_seed()
    fr["sources"].append({"finance_source_id": "fsrc_transit", "account_id": "fsrc_transit", "finance_area_id": AREA,
                          "object_id": AREA, "name": "Деньги в пути", "account_type": "in_transit",
                          "is_active": True, "sort_order": 9})
    fr["period_states"] = [dict(LEGACY_STATE)]
    fr["money_transits"] = [dict(LEGACY_TRANSIT)]
    return fr


class _StatusRowsChecks:
    """Статус / закрытие / открытие — настоящие обработчики; сервер перечитывается после каждого действия."""

    seed = staticmethod(balance_seed)

    def setUp(self):
        super().setUp()
        self.store["payload"]["financeRec"] = self.seed()

    def run_rows(self, name):
        return self.run_scenario(name, harness=ROWS_HARNESS)

    def work_states(self, states):
        return [p for p in states if p.get("finance_area_id") == AREA and p.get("period_key") == WEEK]

    def legacy_states(self, states):
        return [p for p in states if not p.get("finance_area_id")]

    def assert_legacy_untouched(self, states):
        want = [r for r in self.seed()["period_states"] if not r.get("finance_area_id")]
        self.assertEqual(self.legacy_states(states), want)

    def test_status_close_reopen_confirmed_by_server_reread(self):
        out = self.run_rows("status_rereads")
        for b in out["log"]["batches"]:
            for p in b["paths"]:
                self.assertNotIn(p, WIDE, b)
        expect = [("filled", "status"), ("closed", "close"), ("review", "reopen")]
        for step, (status, kind) in zip(out["steps"], expect):
            server = step["server"]
            work = self.work_states(server["period_states"])
            self.assertEqual([p["status"] for p in work], [status], step["action"])
            self.assertIn(kind, [a.get("kind") for a in server["audit"]], step["action"])
            self.assert_legacy_untouched(server["period_states"])
            self.assertEqual([p["status"] for p in self.work_states(step["local_states"])], [status])
            self.assertNotIn("error", [t["kind"] for t in step["toasts"]], step["toasts"])
        close = out["steps"][1]
        self.assertIn("Период закрыт.", [t["msg"] for t in close["toasts"] if t["kind"] == "success"])
        self.assertTrue([o for o in close["server"]["openings"] if o["period_key"] == NEXT])
        self.assertEqual(len(self.work_states(self.stored_fr()["period_states"])), 1)

    def _assert_nothing_saved(self, out):
        fr = self.stored_fr()
        self.assertEqual(self.work_states(fr["period_states"]), [])
        self.assert_legacy_untouched(fr["period_states"])
        self.assertEqual([a["audit_id"] for a in fr["audit"]], ["faud_old"])
        self.assertEqual([o for o in fr["openings"] if o["period_key"] == NEXT], [])
        self.assertEqual(out["local_after"]["period_states"], out["local_before"]["period_states"])
        self.assertEqual(out["local_after"]["audit"], out["local_before"]["audit"])
        self.assertNotIn("success", [t["kind"] for t in out["log"]["toasts"]])
        self.assertTrue(any(t["kind"] == "error" for t in out["log"]["toasts"]), out["log"]["toasts"])

    def test_status_server_500_nothing_saved(self):
        out = self.run_rows("status_500")
        self.assertEqual(out["log"]["batches"][0]["status"], 500)
        self._assert_nothing_saved(out)

    def test_close_server_500_no_close_toast_nothing_saved(self):
        out = self.run_rows("close_500")
        self.assertEqual(out["log"]["batches"][0]["status"], 500)
        self._assert_nothing_saved(out)

    def test_close_conflict_409_keeps_other_employee_status(self):
        out = self.run_rows("close_conflict")
        self.assertEqual(out["other_status"], 200)
        atomic = [b for b in out["log"]["batches"] if b["atomic"]]
        self.assertEqual(atomic[0]["status"], 409, atomic)
        fr = self.stored_fr()
        work = self.work_states(fr["period_states"])
        self.assertEqual([(p["status"], p.get("reviewed_by")) for p in work], [("review", "usr_dir")])
        self.assert_legacy_untouched(fr["period_states"])
        self.assertNotIn("close", [a.get("kind") for a in fr["audit"]])
        self.assertEqual([o for o in fr["openings"] if o["period_key"] == NEXT], [])
        self.assertNotIn("Период закрыт.", [t["msg"] for t in out["log"]["toasts"]])
        self.assertNotEqual([p["status"] for p in self.work_states(out["local_after"]["period_states"])], ["closed"])

    def test_two_employees_status_and_day_actual_both_kept(self):
        out = self.run_rows("status_two_employees")
        self.assertEqual(out["other_status"], 200)
        fr = self.stored_fr()
        self.assertEqual([p["status"] for p in self.work_states(fr["period_states"])], ["filled"])
        self.assertEqual([r["actual_balance"] for r in fr["day_closings"] if r["account_id"] == "fsrc_bank"], [900])
        self.assert_legacy_untouched(fr["period_states"])


@unittest.skipUnless(NODE, "node не найден (WT_NODE_BIN)")
class FrStatusRowsNewFormatTests(_StatusRowsChecks, _FrStage4Harness):
    seed = staticmethod(balance_seed)

    def test_guard_unaddressable_status_row_sends_nothing(self):
        out = self.run_rows("guard_unaddressable_status")
        self.assertEqual([b for b in out["log"]["batches"] if b["atomic"]], [])
        self.assertTrue(any("устаревшем формате" in t["msg"] for t in out["log"]["toasts"]), out["log"]["toasts"])
        self.assertNotIn("Период закрыт.", [t["msg"] for t in out["log"]["toasts"]])
        self.assertEqual([a["audit_id"] for a in self.stored_fr()["audit"]], ["faud_old"])
        self.assertEqual(out["local_after"]["period_states"], out["local_before"]["period_states"])
        self.assertEqual(out["local_after"]["openings"], out["local_before"]["openings"])
        self.assertEqual(out["local_after"]["audit"], out["local_before"]["audit"])


@unittest.skipUnless(NODE, "node не найден (WT_NODE_BIN)")
class FrStatusRowsLegacyFormatTests(_StatusRowsChecks, _FrStage4Harness):
    """Старая строка статуса только с object_id (и старый транзит без transit_id)."""

    seed = staticmethod(legacy_seed)

    def _assert_transit_untouched(self, out):
        self.assertEqual(self.stored_fr()["money_transits"], [LEGACY_TRANSIT])
        for b in out["log"]["batches"]:
            for p in b["paths"]:
                self.assertFalse(p.startswith("financeRec.money_transits"), b)
        self.assertNotIn("financeRec.money_transits", out.get("collected_before") or [])
        self.assertNotIn("financeRec.money_transits", out["collected"])
        self.assertEqual(out["log"]["puts"], 0)
        # Номер старому транзиту по-прежнему присваивается только локально (для экрана).
        self.assertTrue(out["local_transits"][0].get("transit_id"))

    def test_legacy_transit_untouched_on_open_and_wide_save(self):
        self._assert_transit_untouched(self.run_rows("transit_legacy_open_save"))

    def test_legacy_transit_untouched_on_directory_save(self):
        out = self.run_rows("transit_legacy_directory")
        self._assert_transit_untouched(out)
        self.assertEqual(self.stored_fr()["areas"][0]["primary_cash_source_id"], "fsrc_cash2")

    def test_legacy_transit_untouched_on_dividend_save(self):
        out = self.run_rows("transit_legacy_dividend")
        self._assert_transit_untouched(out)
        self.assertTrue([o for o in self.stored_fr()["operations"] if o.get("op_type") == "dividend" and o.get("amount") == 80])

    def test_legacy_transit_untouched_on_balance_save(self):
        out = self.run_rows("transit_legacy_day_actual")
        self._assert_transit_untouched(out)
        self.assertEqual([r["actual_balance"] for r in self.stored_fr()["day_closings"]], [5800])

    def test_new_transit_saved_by_id_and_kept_through_softsync(self):
        out = self.run_rows("transit_send_softsync_inflight")
        transits = self.stored_fr()["money_transits"]
        self.assertEqual(transits[0], LEGACY_TRANSIT)
        self.assertEqual(len(transits), 2)
        new_id = transits[1]["transit_id"]
        self.assertTrue(new_id)
        self.assertEqual(transits[1]["amount_sent"], 700)
        self.assertIn(new_id, [t.get("transit_id") for t in out["local_transits"]])
        self.assertIn("success", [t["kind"] for t in out["log"]["toasts"]])


def ops250_seed():
    seed = balance_seed()
    dates = ("2026-10-27", "2026-10-31", "2026-11-04", "2026-11-06", "2026-11-08")
    for i in range(250):
        sid = "fsrc_cash" if i % 2 else "fsrc_bank"
        seed["operations"].append({
            "finance_operation_id": "fop_bulk_%03d" % i, "object_id": AREA, "finance_area_id": AREA,
            "account_id": sid, "finance_source_id": sid, "date": dates[i % len(dates)], "type": "out",
            "op_type": "expense", "movement_kind": "expense", "amount": 10 + i, "comment": "расход %d" % i,
            "is_deleted": False,
        })
    return seed


@unittest.skipUnless(NODE, "node не найден (WT_NODE_BIN)")
class FrOps250SingleOpBatchTests(_FrStage4Harness):
    """Больше 200 операций: правка одной ячейки / операции отправляет только её и запись журнала."""

    def setUp(self):
        super().setUp()
        self.seed = ops250_seed()
        self.store["payload"]["financeRec"] = json.loads(json.dumps(self.seed))

    def run_rows(self, name):
        return self.run_scenario(name, harness=ROWS_HARNESS)

    def op_versions(self):
        recs = self.store["payload"].get("_wt_records") or {}
        return sorted(p for p in recs if p.startswith("financeRec.operations."))

    def assert_single_op_batches(self, out, count):
        # Неатомарные пакеты — фоновая запись строк отрисовки (перенос, статус недели), без операций.
        for b in out["log"]["batches"]:
            if not b["atomic"]:
                self.assertEqual([p for p in b["paths"] if p.startswith(("financeRec.operations", "financeRec.audit"))], [], b)
        batches = [b for b in out["log"]["batches"] if b["atomic"]]
        self.assertEqual(len(batches), count, batches)
        for b in batches:
            self.assertEqual(b["status"], 200, b)
            self.assertTrue(b["atomic"], b)
            ops = [p for p in b["paths"] if p.startswith("financeRec.operations.")]
            aud = [p for p in b["paths"] if p.startswith("financeRec.audit.")]
            self.assertEqual((len(ops), len(aud), len(b["paths"])), (1, 1, 2), b)
        self.assertEqual(out["log"]["puts"], 0)
        self.assertEqual(out["log"]["singleOps"], [])
        self.assertNotIn("error", [t["kind"] for t in out["log"]["toasts"]], out["log"]["toasts"])

    def assert_bulk_untouched(self, except_ids=()):
        server = {o["finance_operation_id"]: o for o in self.stored_fr()["operations"]}
        for o in self.seed["operations"]:
            if o["finance_operation_id"] in except_ids:
                continue
            self.assertEqual(server[o["finance_operation_id"]], o)

    def new_ops(self, ops):
        seed_ids = {o["finance_operation_id"] for o in self.seed["operations"]}
        return [o for o in ops if o["finance_operation_id"] not in seed_ids]

    def test_revenue_cell_sends_only_new_op_and_audit(self):
        out = self.run_rows("ops250_revenue_cell")
        self.assert_single_op_batches(out, 1)
        self.assert_bulk_untouched()
        new = self.new_ops(self.stored_fr()["operations"])
        self.assertEqual(len(new), 1, new)
        self.assertEqual((new[0]["account_id"], new[0]["date"], new[0]["amount"], new[0]["grid_cell"]),
                         ("fsrc_cash", "2026-11-06", 777, True))
        self.assertEqual(self.op_versions(), ["financeRec.operations." + new[0]["finance_operation_id"]])
        self.assertEqual([o["amount"] for o in self.new_ops(out["reloaded_ops"])], [777])
        self.assertIn("revenue_grid", [a.get("kind") for a in self.stored_fr()["audit"]])

    def test_non_income_cell_sends_only_new_op_and_audit(self):
        out = self.run_rows("ops250_non_income_cell")
        self.assert_single_op_batches(out, 1)
        self.assert_bulk_untouched()
        new = self.new_ops(self.stored_fr()["operations"])
        self.assertEqual(len(new), 1, new)
        self.assertEqual((new[0]["date"], new[0]["amount"], new[0]["comment"]), ("2026-11-06", 150, "возврат поставщика"))
        self.assertEqual([o["amount"] for o in self.new_ops(out["reloaded_ops"])], [150])
        self.assertIn("non_income_grid", [a.get("kind") for a in self.stored_fr()["audit"]])

    def test_edit_op_modal_sends_only_edited_op_and_audit(self):
        out = self.run_rows("ops250_edit_op")
        self.assert_single_op_batches(out, 1)
        self.assertEqual(out["log"]["batches"][0]["paths"][0], "financeRec.operations.fop_bulk_010")
        self.assert_bulk_untouched(except_ids=("fop_bulk_010",))
        server = [o for o in self.stored_fr()["operations"] if o["finance_operation_id"] == "fop_bulk_010"]
        self.assertEqual((server[0]["amount"], server[0]["comment"]), (999, "правка"))
        self.assertEqual(self.op_versions(), ["financeRec.operations.fop_bulk_010"])
        reloaded = [o for o in out["reloaded_ops"] if o["finance_operation_id"] == "fop_bulk_010"]
        self.assertEqual(reloaded[0]["amount"], 999)
        self.assertEqual(len(self.stored_fr()["operations"]), len(self.seed["operations"]))

    def test_two_employees_different_ops_no_false_conflict(self):
        out = self.run_rows("ops250_two_employees")
        self.assert_single_op_batches(out, 2)
        self.assertEqual((out["other_after_a"], out["other_before_a"]), (200, 200))
        self.assert_bulk_untouched(except_ids=("fop_bulk_010", "fop_bulk_100", "fop_bulk_101"))
        amounts = {o["finance_operation_id"]: o["amount"] for o in self.stored_fr()["operations"]}
        self.assertEqual((amounts["fop_bulk_010"], amounts["fop_bulk_100"], amounts["fop_bulk_101"]), (999, 4321, 1234))
        self.assertEqual([o["amount"] for o in self.new_ops(self.stored_fr()["operations"])], [777])
        self.assertNotIn("Конфликт", " ".join(t["msg"] for t in out["log"]["toasts"]))
        reloaded = {o["finance_operation_id"]: o["amount"] for o in out["reloaded_ops"]}
        self.assertEqual((reloaded["fop_bulk_010"], reloaded["fop_bulk_100"], reloaded["fop_bulk_101"]), (999, 4321, 1234))


@unittest.skipUnless(NODE, "node не найден (WT_NODE_BIN)")
class FrRowKeyParityTests(unittest.TestCase):
    """frRowKey (fr-mod.js) и fr_row_key (wt_api.py) дают один и тот же сегмент пути."""

    ROWS = [
        ("openings", {"finance_area_id": AREA, "object_id": AREA, "period_key": WEEK, "finance_source_id": "fsrc_cash", "account_id": "fsrc_cash"}),
        ("openings", {"finance_area_id": AREA, "period_key": NEXT, "finance_source_id": "fsrc_bank"}),
        ("openings", {"object_id": "Объект №2", "period_key": "week:2026-01-01", "account_id": "касса 1"}),
        ("openings", {"finance_area_id": AREA, "period_key": WEEK}),
        ("day_closings", {"object_id": AREA, "date": "2026-11-06", "account_id": "fsrc_cash"}),
        ("day_closings", {"finance_area_id": AREA, "date": "6.11.26", "finance_source_id": "fsrc_cash"}),
        ("day_closings", {"object_id": AREA, "date": "2026-11-06T10:00:00", "account_id": "fsrc_cash"}),
        ("day_closings", {"object_id": AREA, "date": " 06/11/2026 ", "account_id": "fsrc_cash"}),
        ("day_closings", {"object_id": AREA, "date": "", "account_id": "fsrc_cash"}),
        ("period_states", {"finance_area_id": AREA, "period_key": WEEK}),
        ("period_states", {"object_id": AREA, "period_key": "week:2026-12-31"}),
        ("period_states", {"finance_area_id": AREA, "object_id": "farea_other", "period_key": WEEK}),
    ]

    def test_client_and_server_keys_match(self):
        import json
        import subprocess
        import wt_api
        script = r"""
const fs = require("fs");
const src = fs.readFileSync(process.argv[1], "utf8").replace(/\r\n/g, "\n");
function fn(name) {
    const m = new RegExp("^function " + name + "\\(", "m").exec(src);
    return src.slice(m.index, src.indexOf("\n}\n", m.index) + 2);
}
eval(["frRowKeyDate", "frRowKeyPart", "frRowKey"].map(fn).join("\n"));
const rows = JSON.parse(process.argv[2]);
process.stdout.write(JSON.stringify(rows.map(function (r) { return frRowKey(r[0], r[1]); })));
"""
        fr_path = os.path.join(os.path.dirname(HERE), "fr-mod.js")
        proc = subprocess.run([NODE, "-e", script, fr_path, json.dumps(self.ROWS)], capture_output=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", errors="replace"))
        client = json.loads(proc.stdout.decode("utf-8"))
        server = [wt_api.fr_row_key(lk, row) for lk, row in self.ROWS]
        self.assertEqual(client, server)
        self.assertEqual(server[3], "")
        self.assertEqual(server[8], "")
        self.assertEqual(server[4], server[5])
        self.assertEqual(server[4], server[6])
        self.assertEqual(server[4], server[7])
        # Статус: только finance_area_id (как frPeriodState); строка лишь с object_id не адресуется.
        self.assertEqual(server[10], "")
        self.assertEqual(server[11], server[9])


if __name__ == "__main__":
    unittest.main()
