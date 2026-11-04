# -*- coding: utf-8 -*-
"""
Финконтроль: ручная операция / импорт → закрытие периода (atomic) → 409 по журналу → повтор.
Сквозной тест: wt_api на 127.0.0.1 (FakeConn, искусственные данные) + реальные функции
DomainWriter (app.html) и FR (fr-mod.js) в Node. Без live / Timeweb.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from wsgiref.simple_server import WSGIRequestHandler, make_server

import wt_api
from test_wt_api import SAMPLE_APP, FakeConn, SECRET

HERE = os.path.dirname(os.path.abspath(__file__))
HARNESS = os.path.join(HERE, "fr_close_period_atomic_harness.js")
MGR = {
    "user_id": "usr_dir", "display_name": "Финменеджер", "pin": "4444", "role": "manager",
    "is_active": True, "departments": ["shop"],
    "permissions": {"financeRec": "edit", "financeRecSetup": "edit"},
}
WEEK = "2026-W45"


def _find_node():
    cand = [os.environ.get("WT_NODE_BIN") or "", shutil.which("node") or ""]
    local = os.environ.get("LOCALAPPDATA") or ""
    if local:
        cand.append(os.path.join(local, "Programs", "cursor", "resources", "app", "resources", "helpers", "node.exe"))
    for c in cand:
        if c and os.path.isfile(c):
            return c
    return ""


NODE = _find_node()


class _QuietHandler(WSGIRequestHandler):
    def log_message(self, *args):
        pass


@unittest.skipUnless(NODE, "node не найден (WT_NODE_BIN)")
class FrClosePeriodAtomicTests(unittest.TestCase):
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
            "import_batches": [{"import_batch_id": "fimp_old", "batch_key": "old"}],
            "audit": [{"audit_id": "faud_old", "kind": "seed"}],
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

    def run_scenario(self, name):
        env = dict(os.environ)
        env["WT_TEST_BASE_URL"] = self.base
        env["WT_TEST_SCENARIO"] = name
        proc = subprocess.run(
            [NODE, HARNESS], cwd=HERE, env=env, capture_output=True, timeout=120,
        )
        text = proc.stdout.decode("utf-8", errors="replace")
        for line in text.splitlines():
            if line.startswith("RESULT_JSON "):
                return json.loads(line[len("RESULT_JSON "):])
        self.fail("harness: %s\n%s" % (text, proc.stderr.decode("utf-8", errors="replace")))

    # Повторное чтение сервера мимо клиента — из хранилища FakeConn.
    def stored_fr(self):
        return self.store["payload"]["financeRec"]

    def assert_closed_on_server(self, out):
        fr = self.stored_fr()
        self.assertTrue(any(s.get("period_key") == WEEK and s.get("status") == "closed" for s in fr["period_states"]))
        self.assertTrue(any(r.get("period_key") == "2026-W46" and r.get("carried") for r in fr["openings"]))
        aids = [a.get("audit_id") for a in fr["audit"]]
        self.assertIn("faud_close", aids)
        self.assertEqual(len(aids), len(set(aids)), aids)
        self.assertTrue(out["server"]["period_closed"])
        self.assertTrue(out["server"]["opening_carried"])
        self.assertIn("faud_close", out["server"]["audit_ids"])

    def assert_not_closed_on_server(self, out):
        fr = self.stored_fr()
        self.assertFalse(any(s.get("status") == "closed" for s in fr["period_states"]))
        self.assertFalse(any(r.get("period_key") == "2026-W46" for r in fr["openings"]))
        self.assertNotIn("faud_close", [a.get("audit_id") for a in fr["audit"]])
        self.assertFalse(out["server"]["period_closed"])

    def test_manual_op_then_close_retries_whole_atomic_batch(self):
        out = self.run_scenario("manual_then_close")
        self.assertIs(out["manual_ok"], True, out)
        # Запись по id подняла корень журнала; локальная версия корня осталась старой → 409.
        self.assertGreater(out["audit_root_v1"], out["audit_root_v0"])
        self.assertEqual(out["local_audit_root_v"], out["audit_root_v0"])
        first, last = out["close_batches"][0], out["close_batches"][-1]
        self.assertEqual(first["status"], 409, out["close_batches"])
        self.assertEqual(first["conflictPath"], "financeRec.audit")
        self.assertTrue(first["atomic"])
        self.assertEqual(last["status"], 200, out["close_batches"])
        self.assertTrue(last["atomic"])
        self.assertEqual(
            set(last["paths"]),
            {"financeRec.period_states", "financeRec.openings", "financeRec.audit"},
        )
        self.assertIs(out["close_ok"], True, out)
        self.assert_closed_on_server(out)
        self.assertIn("faud_m1", out["server"]["audit_ids"])
        self.assertIn("fop_m1", out["server"]["op_ids"])
        self.assertEqual(out["log"]["confirms"], 0)
        self.assertIn("Период закрыт.", [t["msg"] for t in out["log"]["toasts"]])

    def test_retry_server_failure_is_not_success(self):
        out = self.run_scenario("retry_server_fail")
        self.assertEqual(out["close_batches"][0]["status"], 409, out["close_batches"])
        self.assertIs(out["close_ok"], False, out)
        self.assert_not_closed_on_server(out)
        self.assertFalse(out["local"]["period_closed"])
        self.assertNotIn("faud_close", out["local"]["audit_ids"])
        self.assertNotIn("Период закрыт.", [t["msg"] for t in out["log"]["toasts"]])

    def test_concurrent_period_states_change_is_not_overwritten(self):
        out = self.run_scenario("concurrent_period_states")
        self.assertEqual(out["other_status"], 200)
        self.assertIs(out["close_ok"], False, out)
        self.assertIn("financeRec.period_states", out["log"]["recordDialogs"])
        fr = self.stored_fr()
        self.assertEqual([s.get("status") for s in fr["period_states"]], ["review"])
        self.assertNotIn("faud_close", [a.get("audit_id") for a in fr["audit"]])
        self.assertFalse(any(r.get("period_key") == "2026-W46" for r in fr["openings"]))
        for b in out["close_batches"]:
            self.assertTrue(b["atomic"])
            self.assertNotEqual(b["status"], 200, out["close_batches"])

    # «Мой вариант» в атомарном конфликте: настоящий диалог wtResolveRecordConflict.
    def test_yours_in_atomic_conflict_is_one_atomic_retry(self):
        out = self.run_scenario("yours_atomic_success")
        self.assertEqual(out["other_status"], 200)
        self.assertGreaterEqual(out["log"]["confirms"], 1)
        # Конфликтный путь не пишется отдельным /api/state/op мимо пакета.
        self.assertEqual(out["log"]["singleOps"], [], out["log"])
        last = out["close_batches"][-1]
        self.assertEqual(last["status"], 200, out["close_batches"])
        self.assertTrue(last["atomic"])
        self.assertEqual(
            set(last["paths"]),
            {"financeRec.period_states", "financeRec.openings", "financeRec.audit"},
        )
        self.assertIs(out["close_ok"], True, out)
        self.assert_closed_on_server(out)
        self.assertEqual([s.get("status") for s in self.stored_fr()["period_states"]], ["closed"])

    def test_yours_then_retry_failure_leaves_no_partial_write(self):
        out = self.run_scenario("yours_atomic_retry_fail")
        self.assertEqual(out["other_status"], 200)
        self.assertIs(out["close_ok"], False, out)
        fr = self.stored_fr()
        # Ни одна часть пакета не записана: статус периода — чужой, переноса и журнала закрытия нет.
        self.assertEqual([s.get("status") for s in fr["period_states"]], ["review"])
        self.assertFalse(any(r.get("period_key") == "2026-W46" for r in fr["openings"]))
        self.assertNotIn("faud_close", [a.get("audit_id") for a in fr["audit"]])
        self.assertEqual(out["log"]["singleOps"], [], out["log"])
        self.assertNotIn("Период закрыт.", [t["msg"] for t in out["log"]["toasts"]])

    def test_cancel_in_atomic_conflict_writes_nothing(self):
        out = self.run_scenario("yours_cancel")
        self.assertIs(out["close_ok"], False, out)
        fr = self.stored_fr()
        self.assertEqual([s.get("status") for s in fr["period_states"]], ["review"])
        self.assertNotIn("faud_close", [a.get("audit_id") for a in fr["audit"]])
        self.assertEqual(out["log"]["singleOps"], [], out["log"])
        self.assertNotIn("Период закрыт.", [t["msg"] for t in out["log"]["toasts"]])

    def test_other_user_audit_kept_without_duplicates(self):
        out = self.run_scenario("other_user_audit")
        self.assertEqual(out["other_status"], 200)
        self.assertIs(out["close_ok"], True, out)
        self.assert_closed_on_server(out)
        aids = out["server"]["audit_ids"]
        for aid in ("faud_old", "faud_m1", "faud_other", "faud_close"):
            self.assertEqual(aids.count(aid), 1, aids)

    def test_import_then_close(self):
        out = self.run_scenario("import_then_close")
        self.assertEqual(out["import_status"], 200, out)
        self.assertIs(out["close_ok"], True, out)
        self.assert_closed_on_server(out)
        self.assertIn("fop_imp1", out["server"]["op_ids"])
        self.assertIn("fop_imp2", out["server"]["op_ids"])
        self.assertIn("fimp_t1", out["server"]["batch_ids"])
        self.assertIn("faud_imp1", out["server"]["audit_ids"])

    def test_ops_disabled_manual_op_not_saved(self):
        os.environ["WT_OPS_ENABLED"] = "0"
        out = self.run_scenario("ops_disabled_manual")
        self.assertIs(out["manual_ok"], False, out)
        self.assertNotIn("fop_m1", out["server"]["op_ids"])
        self.assertNotIn("fop_m1", [o.get("finance_operation_id") for o in self.stored_fr()["operations"]])
        # Несохранённая операция не остаётся на экране как «записанная».
        self.assertNotIn("fop_m1", out["local"]["op_ids"])
        self.assertIn("fop_a", out["local"]["op_ids"])
        self.assertNotIn("faud_m1", out["local"]["audit_ids"])
        self.assertEqual(out["log"]["puts"], 0)
        self.assertTrue(any(t["kind"] == "error" for t in out["log"]["toasts"]))

    # --- Откат несохранённой ручной операции (fr-mod.js frPersistOpsById) ---

    def assert_no_success_toast(self, out):
        kinds = [t["kind"] for t in out["log"]["toasts"]]
        self.assertNotIn("success", kinds, out["log"]["toasts"])
        self.assertIn("error", kinds, out["log"]["toasts"])

    def assert_server_untouched(self, out):
        fr = self.stored_fr()
        self.assertEqual([(o["finance_operation_id"], o["amount"]) for o in fr["operations"]], [("fop_a", 100)])
        self.assertEqual([a["audit_id"] for a in fr["audit"]], ["faud_old"])
        self.assertEqual(out["server_amounts"], {"fop_a": 100})

    def test_rollback_create_on_network_error(self):
        out = self.run_scenario("rollback_create_network")
        self.assertIs(out["result"], False, out)
        self.assertEqual(out["local_amounts"], {"fop_a": 100}, out)
        self.assertNotIn("faud_m1", out["local"]["audit_ids"])
        self.assert_server_untouched(out)
        self.assert_no_success_toast(out)

    def test_rollback_create_on_server_failure(self):
        out = self.run_scenario("rollback_create_server_500")
        self.assertIs(out["result"], False, out)
        self.assertEqual(out["local_amounts"], {"fop_a": 100}, out)
        self.assertNotIn("faud_m1", out["local"]["audit_ids"])
        self.assert_server_untouched(out)
        self.assert_no_success_toast(out)

    def test_rollback_edit_restores_previous_value(self):
        out = self.run_scenario("rollback_edit_network")
        self.assertIs(out["result"], False, out)
        self.assertEqual(out["local_amounts"], {"fop_a": 100}, out)
        self.assertNotIn("faud_e1", out["local"]["audit_ids"])
        self.assert_server_untouched(out)
        self.assert_no_success_toast(out)

    def test_rollback_keeps_newer_local_edit_and_foreign_op(self):
        out = self.run_scenario("rollback_keeps_newer_local")
        self.assertIs(out["result"], False, out)
        # Более новая локальная правка и чужая операция (SoftSync) не затираются откатом.
        self.assertEqual(out["local_amounts"], {"fop_a": 777, "fop_other": 42}, out)
        self.assert_server_untouched(out)
        self.assert_no_success_toast(out)

    def test_rollback_paste_queue(self):
        out = self.run_scenario("rollback_paste_queue")
        self.assertEqual(out["queued"], ["queued", "queued"], out)
        self.assertIs(out["result"], False, out)
        self.assertEqual(out["local_amounts"], {"fop_a": 100}, out)
        self.assertNotIn("faud_p1", out["local"]["audit_ids"])
        self.assert_server_untouched(out)
        self.assert_no_success_toast(out)

    def test_rollback_when_save_busy(self):
        out = self.run_scenario("rollback_busy")
        self.assertIs(out["result"], False, out)
        self.assertEqual(out["local_amounts"], {"fop_a": 100}, out)
        self.assertNotIn("faud_b1", out["local"]["audit_ids"])
        # Флаг занятости принадлежит идущему сохранению — не сбрасывается.
        self.assertTrue(out["busy_after"])
        self.assertEqual(out["log"]["batches"], [])
        self.assert_server_untouched(out)

    def test_edit_success_control(self):
        out = self.run_scenario("rollback_success_control")
        self.assertIs(out["result"], True, out)
        self.assertEqual(out["local_amounts"], {"fop_a": 555}, out)
        self.assertEqual(out["server_amounts"], {"fop_a": 555}, out)
        self.assertIn("faud_e1", out["server"]["audit_ids"])


if __name__ == "__main__":
    unittest.main()
