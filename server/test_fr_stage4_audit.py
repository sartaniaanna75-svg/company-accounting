# -*- coding: utf-8 -*-
"""
Этап 4 Финконтроля — аудит широких saveApp() (остатки, дивиденды, транзиты).
Рабочий код не меняется: тесты описывают безопасное поведение; подтверждённые дефекты
помечены expectedFailure (после исправления пометку снять).
Локальный wt_api на 127.0.0.1 (FakeConn, искусственные данные) + настоящие функции
DomainWriter из app.html в Node. Без live / Timeweb.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from wsgiref.simple_server import make_server

import wt_api
from test_wt_api import SAMPLE_APP, FakeConn, SECRET
from test_fr_close_period_atomic import MGR, NODE, WEEK, _QuietHandler

HERE = os.path.dirname(os.path.abspath(__file__))
HARNESS = os.path.join(HERE, "fr_stage4_audit_harness.js")
WIDE_FR_PATHS = ("financeRec.audit", "financeRec.openings", "financeRec.period_states", "financeRec.operations")


class _FrStage4Harness(unittest.TestCase):
    def setUp(self):
        self._env_backup = dict(os.environ)
        os.environ["DATABASE_URL"] = "postgresql://worktime:" + SECRET + "@127.0.0.1:5432/worktime"
        os.environ["WT_PIN_PEPPER"] = "unit-pepper"
        os.environ["WT_SCRYPT_N"] = "16"
        os.environ["WT_OPS_ENABLED"] = "1"
        os.environ.pop("WT_API_TOKEN", None)
        os.environ.pop("WT_COOKIE_SECURE", None)
        wt_api.LOGIN_FAILS.clear()
        payload = json.loads(json.dumps(SAMPLE_APP))
        payload["users"].append(json.loads(json.dumps(MGR)))
        payload["financeRec"] = {
            "operations": [{"finance_operation_id": "fop_a", "amount": 100, "is_deleted": False}],
            "import_batches": [],
            "audit": [{"audit_id": "faud_old", "kind": "seed"}],
            "openings": [],
            "period_states": [],
            "day_closings": [],
            "money_transits": [],
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
        self.httpd = make_server("127.0.0.1", 0, wt_api.application, handler_class=_QuietHandler)
        self.base = "http://127.0.0.1:%d" % self.httpd.server_address[1]
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self._patch.stop()
        os.environ.clear()
        os.environ.update(self._env_backup)
        wt_api.LOGIN_FAILS.clear()

    def run_scenario(self, name, confirms=(), harness=None):
        env = dict(os.environ)
        env["WT_TEST_BASE_URL"] = self.base
        env["WT_TEST_SCENARIO"] = name
        env["WT_TEST_CONFIRMS"] = json.dumps(list(confirms))
        proc = subprocess.run([NODE, harness or HARNESS], cwd=HERE, env=env, capture_output=True, timeout=120)
        text = proc.stdout.decode("utf-8", errors="replace")
        for line in text.splitlines():
            if line.startswith("RESULT_JSON "):
                out = json.loads(line[len("RESULT_JSON "):])
                if os.environ.get("WT_STAGE4_DUMP"):
                    print("\n=== %s ===\n%s" % (name, json.dumps(out, ensure_ascii=False, indent=1)))
                return out
        self.fail("harness: %s\n%s" % (text, proc.stderr.decode("utf-8", errors="replace")))

    def stored_fr(self):
        return self.store["payload"]["financeRec"]


@unittest.skipUnless(NODE, "node не найден (WT_NODE_BIN)")
class FrStage4WideSaveAuditTests(_FrStage4Harness):
    # --- Остатки: frSaveDayActual / frSaveWeekActualBalance (day_closings), frSaveOpening (openings) ---

    # Этап 4Б: настоящие frSaveDayActual / frSaveOpening (стенд fr_stage4b_rows_harness.js).
    def _run_rows(self, name):
        from test_fr_stage4b_rows import ROWS_HARNESS, balance_seed
        self.store["payload"]["financeRec"] = balance_seed()
        return self.run_scenario(name, harness=ROWS_HARNESS)

    def test_day_actual_does_not_drop_other_employee_row(self):
        out = self._run_rows("day_actual_two_employees")
        self.assertEqual(out["other_status"], 200)
        self.assertNotIn("financeRec.day_closings", out["collected"])
        accounts = [r.get("account_id") for r in self.stored_fr()["day_closings"]]
        self.assertIn("fsrc_cash", accounts, out)
        self.assertIn("fsrc_bank", accounts, out)

    def test_opening_does_not_drop_other_employee_row(self):
        out = self._run_rows("opening_two_employees")
        self.assertEqual(out["other_status"], 200)
        week = [r for r in self.stored_fr()["openings"] if r.get("period_key") == "week:2026-11-05"]
        srcs = [r.get("finance_source_id") for r in week if r.get("is_manual")]
        self.assertIn("fsrc_cash", srcs, out)
        self.assertIn("fsrc_bank", srcs, out)

    # --- Побочные изменения отрисовки уходят в чужой широкий saveApp ---

    def test_dividend_save_does_not_send_render_side_effects(self):
        out = self.run_scenario("render_sweep_no_conflict")
        paths = [p for b in out["log"]["batches"] for p in b["paths"]]
        self.assertNotIn("financeRec.period_states", paths, out["log"]["batches"])
        self.assertNotIn("financeRec.openings", paths, out["log"]["batches"])
        for wide in WIDE_FR_PATHS:
            self.assertNotIn(wide, paths, out["log"]["batches"])

    def test_dividend_save_cannot_reopen_period_closed_by_other(self):
        out = self.run_scenario("render_sweep_period_states", confirms=[True])
        self.assertEqual(out["other_status"], 200)
        states = [(s.get("period_key"), s.get("status")) for s in self.stored_fr()["period_states"]]
        self.assertIn((WEEK, "closed"), states, out)

    # --- Транзит: операция и запись money_transits должны сохраняться вместе ---

    def test_transit_send_is_all_or_nothing(self):
        out = self.run_scenario("transit_send_partial")
        self.assertEqual(out["other_status"], 200)
        self.assertIsNot(out.get("save_result"), True, out)
        fr = self.stored_fr()
        has_op = any(o.get("transit_role") == "send" for o in fr["operations"])
        has_transit = bool(fr["money_transits"])
        self.assertEqual(has_op, has_transit, out)
        self.assertFalse(has_op, out)

    # --- Дивиденды: отказ сети ---

    def test_dividend_network_error_is_not_left_as_saved(self):
        out = self.run_scenario("dividend_network_error")
        self.assertIsNot(out.get("save_result"), True, out)
        self.assertEqual(out["server_op_ids"], ["fop_a"])
        self.assertTrue(any(t["kind"] == "error" for t in out["log"]["toasts"]))
        # Безопасно: несохранённая операция не остаётся на экране как записанная, окно не закрыто.
        self.assertEqual(out["local_op_ids"], ["fop_a"], out)
        self.assertEqual(out["local_audit_ids"], ["faud_old"], out)
        self.assertFalse(out["log"].get("modalClosed"), out["log"])
        self.assertNotIn("success", [t["kind"] for t in out["log"]["toasts"]])

    # --- Дивиденды (этап 4А): настоящий saveFrDividend ---

    def test_dividend_saved_by_id_in_one_atomic_batch(self):
        out = self.run_scenario("dividend_success")
        self.assertIs(out.get("save_result"), True, out)
        self.assertEqual(len(out["local_div_ids"]), 1, out)
        div_id = out["local_div_ids"][0]
        batches = out["log"]["batches"]
        self.assertEqual(len(batches), 1, batches)
        self.assertTrue(batches[0]["atomic"])
        self.assertEqual(batches[0]["status"], 200)
        paths = batches[0]["paths"]
        self.assertIn("financeRec.operations." + div_id, paths)
        self.assertEqual(len([p for p in paths if p.startswith("financeRec.audit.")]), 1, paths)
        self.assertEqual(len(paths), 2, paths)
        fr = self.stored_fr()
        rec = next(o for o in fr["operations"] if o.get("finance_operation_id") == div_id)
        self.assertEqual(rec["amount"], 300)
        self.assertEqual(rec["movement_kind"], "dividend")
        self.assertIn("dividend_add", [a.get("kind") for a in fr["audit"]])
        self.assertEqual(out["log"].get("modalClosed"), 1)
        self.assertEqual(out["log"]["singleOps"], [])

    def test_dividend_server_failure_rolls_back_and_keeps_modal(self):
        out = self.run_scenario("dividend_server_500")
        self.assertIsNot(out.get("save_result"), True, out)
        self.assertEqual(out["local_op_ids"], ["fop_a"], out)
        self.assertEqual(out["local_audit_ids"], ["faud_old"], out)
        self.assertEqual(out["server_op_ids"], ["fop_a"])
        self.assertFalse(out["log"].get("modalClosed"), out["log"])
        self.assertNotIn("success", [t["kind"] for t in out["log"]["toasts"]])

    def test_dividend_edit_conflict_is_409_without_overwrite(self):
        out = self.run_scenario("dividend_edit_conflict")
        self.assertEqual(out["seed_status"], 200)
        self.assertEqual(out["other_status"], 200)
        self.assertIsNot(out.get("save_result"), True, out)
        # Сервер хранит вариант другого сотрудника; локальная правка отменена, журнал не дописан.
        self.assertEqual(out["server_amount"], 77, out)
        self.assertNotIn("dividend_edit", out["server_audit_kinds"])
        self.assertEqual(out["local_amount"], 50, out)
        self.assertNotIn("dividend_edit", out["local_audit_kinds"])
        self.assertFalse(out["log"].get("modalClosed"), out["log"])
        self.assertEqual(out["log"]["singleOps"], [], out["log"])

    # --- Переводы: запрещённое направление проверяется до изменения данных ---

    def test_quick_transfer_from_transit_creates_nothing(self):
        out = self.run_scenario("transfer_quick_from_transit")
        self.assertEqual(out["ops_after"], out["ops_before"], out)
        self.assertEqual(out["server_op_count"], 1, out)
        self.assertEqual(out["log"]["batches"], [], out["log"])
        self.assertTrue(any(t["kind"] == "error" for t in out["log"]["toasts"]))

    def test_modal_transfer_from_transit_leaves_no_local_draft(self):
        out = self.run_scenario("transfer_modal_from_transit")
        self.assertEqual(out["ops_after"], out["ops_before"], out)
        self.assertEqual(out["collected_after"], [], out)
        self.assertEqual(out["log"]["batches"], [], out["log"])
        self.assertTrue(any(t["kind"] == "error" for t in out["log"]["toasts"]))


TRANSIT_ID_RE = r"^ftr_\d{3}_[a-z0-9]+$"


@unittest.skipUnless(NODE, "node не найден (WT_NODE_BIN)")
class FrStage4ATransitTests(_FrStage4Harness):
    """Этап 4А: транзиты и переводы — узкие атомарные пакеты по id (настоящие обработчики fr-mod.js)."""

    def assert_narrow(self, out):
        paths = [p for b in out["log"]["batches"] for p in b["paths"]]
        for wide in WIDE_FR_PATHS + ("financeRec.money_transits",):
            self.assertNotIn(wide, paths, out["log"]["batches"])
        for b in out["log"]["batches"]:
            self.assertTrue(b["atomic"], b)
        self.assertEqual(out["log"]["singleOps"], [], out["log"])
        self.assertEqual(out["log"]["puts"], 0)
        return paths

    def assert_untouched_after_fail(self, out):
        fr = self.stored_fr()
        self.assertEqual([o["finance_operation_id"] for o in fr["operations"]], ["fop_a"], out)
        self.assertEqual(fr["money_transits"], [], out)
        self.assertEqual([a["audit_id"] for a in fr["audit"]], ["faud_old"], out)
        self.assertEqual(out["local_op_ids"], ["fop_a"], out)
        self.assertEqual(out["local_transits"], [], out)
        self.assertEqual(out["local_audit_ids"], ["faud_old"], out)
        self.assertFalse(out["log"].get("modalClosed"), out["log"])
        self.assertNotIn("success", [t["kind"] for t in out["log"]["toasts"]])
        self.assertTrue(any(t["kind"] == "error" for t in out["log"]["toasts"]))

    # 1, 2, 10, 12. Создание транзита при отправке в путь; повторное чтение сервера.
    def test_send_creates_transit_in_one_atomic_batch(self):
        out = self.run_scenario("transit_send_ok")
        self.assertIs(out.get("save_result"), True, out)
        tid = out["transit_id"]
        self.assertRegex(tid, TRANSIT_ID_RE)
        paths = self.assert_narrow(out)
        self.assertEqual(len(out["log"]["batches"]), 1)
        self.assertEqual(len(paths), 3, paths)
        op_id = out["send_op"]["finance_operation_id"]
        self.assertIn("financeRec.operations." + op_id, paths)
        self.assertIn("financeRec.money_transits." + tid, paths)
        self.assertEqual(len([p for p in paths if p.startswith("financeRec.audit.")]), 1)
        fr = self.stored_fr()
        t = next(x for x in fr["money_transits"] if x["transit_id"] == tid)
        self.assertEqual((t["amount_sent"], t["amount_closed"], t["amount_open"], t["status"]), (700, 0, 700, "open"))
        self.assertEqual(t["send_operation_id"], op_id)
        op = next(o for o in fr["operations"] if o["finance_operation_id"] == op_id)
        self.assertEqual((op["transit_id"], op["transit_role"], op["amount"]), (tid, "send", 700))
        self.assertIn("transit_send", [a.get("kind") for a in fr["audit"]])
        self.assertEqual(out["log"].get("modalClosed"), 1)
        self.assertIn("success", [x["kind"] for x in out["log"]["toasts"]])
        self.assertGreater(out["transit_v"], 0)
        self.assertEqual(out["reloaded_transits"], out["server_transits"])
        self.assertEqual(out["reloaded_v"], out["transit_v"])

    # 3, 4, 10. Частичное поступление и полное закрытие после F5.
    def test_partial_then_full_close(self):
        out = self.run_scenario("transit_close_partial_full")
        self.assertIs(out["send_result"], True, out)
        self.assertIs(out["partial_result"], True, out)
        self.assertIs(out["full_result"], True, out)
        tid = out["transit_id"]
        self.assert_narrow(out)
        partial = next(t for t in out["after_partial"] if t["transit_id"] == tid)
        self.assertEqual((partial["amount_closed"], partial["amount_open"], partial["status"]), (300, 400, "partial"))
        self.assertEqual(len(partial["closes"]), 1)
        t = next(x for x in self.stored_fr()["money_transits"] if x["transit_id"] == tid)
        self.assertEqual((t["amount_closed"], t["amount_open"], t["status"]), (700, 0, "closed"))
        self.assertEqual([c["amount"] for c in t["closes"]], [300, 400])
        ops = {o["finance_operation_id"]: o for o in self.stored_fr()["operations"]}
        for c in t["closes"]:
            op = ops[c["operation_id"]]
            self.assertEqual((op["transit_id"], op["transit_role"], op["amount"]), (tid, "close", c["amount"]))
        for b in out["log"]["batches"][1:]:
            self.assertEqual(len(b["paths"]), 3, b)
            self.assertEqual(b["status"], 200, b)
        self.assertEqual(len(self.stored_fr()["money_transits"]), 1)

    # 5. Два сотрудника создают разные транзиты.
    def test_two_employees_create_different_transits(self):
        out = self.run_scenario("transit_two_create")
        self.assertEqual(out["other_status"], 200)
        self.assertIs(out.get("save_result"), True, out)
        self.assert_narrow(out)
        ids = [t["transit_id"] for t in self.stored_fr()["money_transits"]]
        self.assertEqual(len(ids), 2, ids)
        self.assertIn("ftr_001_bbbbb", ids)
        self.assertEqual(len(set(ids)), 2)

    # 6, 7. Два сотрудника правят один транзит → 409, без перезаписи и с откатом.
    def test_two_employees_edit_same_transit_conflict(self):
        out = self.run_scenario("transit_two_edit_conflict")
        self.assertIs(out["send_result"], True, out)
        self.assertEqual(out["other_status"], 200)
        self.assertIsNot(out.get("close_result"), True, out)
        self.assert_narrow(out)
        self.assertEqual(out["log"]["batches"][-1]["status"], 409, out["log"]["batches"])
        tid = out["transit_id"]
        t = next(x for x in self.stored_fr()["money_transits"] if x["transit_id"] == tid)
        self.assertEqual(t["amount_closed"], 100)
        self.assertEqual(len(t["closes"]), 1)
        self.assertFalse(any(o.get("transit_role") == "close" for o in self.stored_fr()["operations"]))
        self.assertNotIn("transit_close", [a.get("kind") for a in self.stored_fr()["audit"]])
        local = next(x for x in out["local_transits"] if x["transit_id"] == tid)
        self.assertEqual(local, out["local_before"])
        self.assertEqual(len(out["local_op_ids"]), 2, out)
        self.assertEqual(out["log"].get("modalClosed"), 1)
        self.assertTrue(any("Конфликт" in x["msg"] for x in out["log"]["toasts"]))

    # 8. Ошибка сети.
    def test_send_network_error_rolls_back(self):
        out = self.run_scenario("transit_send_network")
        self.assertIsNot(out.get("save_result"), True, out)
        self.assert_untouched_after_fail(out)

    # 9. Отказ 2-й или 3-й части атомарного пакета.
    def test_send_fail_second_part_nothing_saved(self):
        out = self.run_scenario("transit_send_fail_op2")
        self.assertIsNot(out.get("save_result"), True, out)
        self.assertEqual(out["log"]["batches"][0]["status"], 409)
        self.assert_untouched_after_fail(out)

    def test_send_fail_third_part_nothing_saved(self):
        out = self.run_scenario("transit_send_fail_op3")
        self.assertIsNot(out.get("save_result"), True, out)
        self.assertEqual(out["log"]["batches"][0]["status"], 409)
        self.assert_untouched_after_fail(out)

    def test_rollback_keeps_newer_local_edits(self):
        out = self.run_scenario("transit_rollback_keeps_newer")
        self.assertIs(out["seed_result"], True, out)
        self.assertIsNot(out.get("save_result"), True, out)
        comments = {t["transit_id"]: t.get("comment") for t in out["local_transits"]}
        self.assertEqual(comments[out["keep_id"]], "правка во время сохранения")
        # Новый транзит изменён после отправки — не удалён откатом; операция и журнал попытки откатаны.
        self.assertIn("новая правка нового транзита", comments.values())
        self.assertEqual(len(out["local_op_ids"]), 2, out)
        self.assertEqual(len(self.stored_fr()["money_transits"]), 1)

    def test_double_click_sends_once(self):
        out = self.run_scenario("transit_double_click")
        self.assertIs(out["first"], True, out)
        self.assertIsNot(out.get("second"), True, out)
        self.assertTrue(any("ещё выполняется" in x["msg"] for x in out["log"]["toasts"]), out["log"]["toasts"])
        self.assertEqual(len(out["log"]["batches"]), 1, out["log"]["batches"])
        self.assertEqual(len(self.stored_fr()["money_transits"]), 1)
        self.assertEqual(len(out["local_transits"]), 1)

    # 11. Удаление связанной операции.
    def test_delete_send_op_writes_transit_by_id(self):
        out = self.run_scenario("transit_delete_send")
        self.assertIs(out["delete_result"], True, out)
        paths = self.assert_narrow(out)
        tid = out["transit_id"]
        last = out["log"]["batches"][-1]["paths"]
        self.assertIn("financeRec.operations." + out["send_op_id"], last)
        self.assertIn("financeRec.money_transits." + tid, last)
        self.assertEqual(len(last), 3, last)
        op = next(o for o in self.stored_fr()["operations"] if o["finance_operation_id"] == out["send_op_id"])
        self.assertTrue(op["is_deleted"])
        t = next(x for x in self.stored_fr()["money_transits"] if x["transit_id"] == tid)
        self.assertEqual((t["amount_sent"], t["status"]), (0, "closed"))
        self.assertTrue(paths)

    def test_delete_send_op_failure_restores_transit_by_id(self):
        out = self.run_scenario("transit_delete_send_fail")
        self.assertIsNot(out["delete_result"], True, out)
        self.assert_narrow(out)
        self.assertFalse(out["local_send_op"]["is_deleted"])
        local = next(x for x in out["local_transits"] if x["transit_id"] == out["transit_id"])
        self.assertEqual((local["amount_sent"], local["status"]), (700, "open"))
        server = next(x for x in self.stored_fr()["money_transits"] if x["transit_id"] == out["transit_id"])
        self.assertEqual(server["amount_sent"], 700)
        self.assertEqual(len(out["local_audit_ids"]), len(out["server_audit_ids"]))

    def test_delete_send_with_receipts_is_blocked(self):
        out = self.run_scenario("transit_delete_blocked_partial")
        self.assertIs(out["close_result"], True, out)
        self.assertEqual(len(out["log"]["batches"]), 2, out["log"]["batches"])
        self.assertTrue(any("поступления" in x["msg"] for x in out["log"]["toasts"]))
        op = next(o for o in self.stored_fr()["operations"] if o["finance_operation_id"] == out["target_id"])
        self.assertFalse(op.get("is_deleted"))

    def test_delete_close_op_is_narrow(self):
        out = self.run_scenario("transit_delete_close_op")
        self.assertIs(out["delete_result"], True, out)
        self.assert_narrow(out)
        last = out["log"]["batches"][-1]["paths"]
        self.assertIn("financeRec.operations." + out["close_op_id"], last)
        # Существующее правило: удаление поступления не пересчитывает транзит — его запись не отправляется.
        self.assertFalse(any(p.startswith("financeRec.money_transits.") for p in last), last)

    def test_legacy_transit_without_id_is_not_duplicated(self):
        out = self.run_scenario("transit_legacy_no_id")
        self.assertEqual(out["other_status"], 200)
        self.assertIsNot(out.get("close_result"), True, out)
        self.assertEqual(out["log"]["batches"], [], out["log"])
        stored = self.stored_fr()["money_transits"]
        self.assertEqual(len(stored), 1)
        self.assertNotIn("transit_id", stored[0])
        self.assertEqual(stored[0]["amount_closed"], 0)
        self.assertEqual(len(out["local_op_ids"]), out["ops_before"])
        self.assertEqual(out["local_transits"][0]["amount_closed"], 0)
        self.assertTrue(any("миграция" in x["msg"] for x in out["log"]["toasts"]))

    def test_quick_transfer_to_transit_creates_transit_atomically(self):
        out = self.run_scenario("transit_quick_to_transit")
        self.assertIs(out.get("save_result"), True, out)
        paths = self.assert_narrow(out)
        self.assertEqual(len(paths), 3, paths)
        self.assertEqual(len(self.stored_fr()["money_transits"]), 1)
        self.assertRegex(self.stored_fr()["money_transits"][0]["transit_id"], TRANSIT_ID_RE)

    def test_edit_send_amount_below_received_saved_atomically(self):
        # Правило расчёта не меняется: фиксируем, что сохранение атомарно и поступления не теряются.
        out = self.run_scenario("transit_edit_send_below_received")
        self.assertIs(out["close_result"], True, out)
        self.assertIs(out["edit_result"], True, out)
        self.assert_narrow(out)
        self.assertEqual(len(out["log"]["batches"][-1]["paths"]), 3, out["log"]["batches"][-1])
        t = next(x for x in self.stored_fr()["money_transits"] if x["transit_id"] == out["transit_id"])
        self.assertEqual(sum(c["amount"] for c in t["closes"]), 500)
        self.assertEqual(t, next(x for x in out["local_transits"] if x["transit_id"] == out["transit_id"]))


if __name__ == "__main__":
    unittest.main()
