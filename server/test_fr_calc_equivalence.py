# -*- coding: utf-8 -*-
"""
Финансовые расчёты не меняются от технических исправлений сохранения: одна и та же
последовательность действий на искусственных данных в fr-mod.js из git HEAD и в рабочей копии
даёт одинаковые данные и показатели (остатки, суммы в пути, дивиденды, сверка недели).
Без сервера и без рабочей БД.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest

from test_fr_close_period_atomic import NODE

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
HARNESS = os.path.join(HERE, "fr_calc_equivalence_harness.js")


def _head_fr_mod():
    try:
        data = subprocess.run(["git", "show", "HEAD:fr-mod.js"], cwd=ROOT, capture_output=True, timeout=60, check=True).stdout
    except Exception:
        return None
    fd, path = tempfile.mkstemp(suffix=".fr-mod.head.js")
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    return path


@unittest.skipUnless(NODE, "node не найден (WT_NODE_BIN)")
class FrCalcEquivalenceTests(unittest.TestCase):
    def run_harness(self, scenario):
        base = _head_fr_mod()
        if not base:
            self.skipTest("git HEAD недоступен")
        try:
            env = dict(os.environ)
            env["WT_FR_BASE"] = base
            env["WT_FR_CUR"] = os.path.join(ROOT, "fr-mod.js")
            env["WT_FR_SCENARIO"] = scenario
            proc = subprocess.run([NODE, HARNESS], cwd=HERE, env=env, capture_output=True, timeout=180)
        finally:
            os.unlink(base)
        text = proc.stdout.decode("utf-8", errors="replace")
        for line in text.splitlines():
            if line.startswith("RESULT_JSON "):
                return json.loads(line[len("RESULT_JSON "):])
        self.fail("harness: %s\n%s" % (text, proc.stderr.decode("utf-8", errors="replace")))

    def test_transits_transfers_dividends_same_results_as_head(self):
        out = self.run_harness("transits")
        for step in out["steps"]:
            self.assertTrue(step["metrics_equal"], step)
            self.assertTrue(step["equal"], step)
            self.assertEqual(step["base_errors"], step["cur_errors"], step)
            # Единственное отличие этапа 4А: отклонённый перевод не оставляет пустую удалённую операцию.
            self.assertEqual(step["cur_abandoned"], 0, step)
        by = {s["name"]: s for s in out["steps"]}
        # Сценарий действительно проходит через транзиты (не пустое сравнение).
        partial = by["close_partial_300"]["transits"][0]
        self.assertEqual((partial["amount_sent"], partial["amount_closed"], partial["status"]), (700, 300, "partial"))
        edited = by["edit_send_below_received"]["transits"][0]
        self.assertEqual((edited["amount_sent"], edited["amount_closed"]), (200, 200))
        self.assertEqual(len(by["delete_send_without_receipts"]["transits"]), 3)

    def test_balances_statuses_carry_same_results_as_head(self):
        out = self.run_harness("balances")
        for step in out["steps"]:
            self.assertTrue(step["metrics_equal"], step)
            self.assertTrue(step["equal"], step)
            self.assertEqual(step["base_errors"], step["cur_errors"], step)
        by = {s["name"]: s for s in out["steps"]}
        # Сценарий действительно меняет остатки, статусы и перенос (не пустое сравнение).
        self.assertTrue(by["render_week"]["openings"])
        self.assertTrue(by["render_week"]["period_states"])
        self.assertEqual(len(by["day_actual_cash"]["day_closings"]), 1)
        self.assertEqual([p["status"] for p in by["close_period_forced"]["period_states"]
                          if p["period_key"] == "week:2026-11-05"], ["closed"])
        self.assertEqual([p["status"] for p in by["reopen_period"]["period_states"]
                          if p["period_key"] == "week:2026-11-05"], ["review"])
        self.assertIn("opening_edit", by["opening_bank_edit"]["audit_kinds"])
        self.assertTrue(by["week_actual_cash2_refused"]["cur_errors"])
        self.assertTrue([o for o in by["push_carry"]["openings"] if o["period_key"] == "week:2026-11-12"])

    def test_balances_with_legacy_status_and_transit_same_results_as_head(self):
        out = self.run_harness("balances_legacy")
        for step in out["steps"]:
            self.assertTrue(step["metrics_equal"], step)
            self.assertTrue(step["equal"], step)
            self.assertEqual(step["base_errors"], step["cur_errors"], step)
        by = {s["name"]: s for s in out["steps"]}
        legacy = [p for p in by["reopen_period"]["period_states"] if not p.get("finance_area_id")]
        self.assertEqual([(p["status"], p["closed_by"]) for p in legacy], [("closed", "old_user")])
        work = [p for p in by["reopen_period"]["period_states"]
                if p.get("finance_area_id") and p["period_key"] == "week:2026-11-05"]
        self.assertEqual([p["status"] for p in work], ["review"])
        self.assertEqual(len(by["reopen_period"]["transits"]), 1)


if __name__ == "__main__":
    unittest.main()
