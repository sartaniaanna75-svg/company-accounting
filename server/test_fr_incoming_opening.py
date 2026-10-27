# -*- coding: utf-8 -*-
"""Входящие остатки недели: кассы без in_actual_balance → 0 (зеркало fr-mod.js)."""

from __future__ import annotations

import unittest
from pathlib import Path

FR_MOD = Path(__file__).resolve().parents[1].joinpath("fr-mod.js")


def owns_incoming(src):
    if not src:
        return False
    return src.get("in_actual_balance") is not False


def week_incoming(sources_by_id, openings, area_id, period_key, source_id):
    src = sources_by_id.get(source_id)
    if src and not owns_incoming(src):
        return 0.0
    for row in openings:
        if (
            row
            and row.get("finance_area_id") == area_id
            and row.get("period_key") == period_key
            and row.get("finance_source_id") == source_id
        ):
            return float(row.get("value") or 0)
    return 0.0


def area_week_opening(sources, openings, area_id, period_key):
    total = 0.0
    for s in sources:
        if not s or s.get("finance_area_id") != area_id:
            continue
        if not owns_incoming(s):
            continue
        total += week_incoming(
            {x["finance_source_id"]: x for x in sources},
            openings,
            area_id,
            period_key,
            s["finance_source_id"],
        )
    return round(total, 2)


def carry_value(src, computed):
    if not owns_incoming(src):
        return 0.0
    return float(computed)


class IncomingOpeningLogicTests(unittest.TestCase):
    def test_fr_mod_helpers_present(self):
        src = FR_MOD.read_text(encoding="utf-8")
        self.assertIn("function frWeekIncomingOpening(", src)
        self.assertIn("function frSourceOwnsIncomingOpening(", src)
        carry = src[src.find("function frCarryIfNeeded(") : src.find("function frSourceTotals(")]
        self.assertIn("frSourceOwnsIncomingOpening", carry)
        push = src[src.find("function frPushCarry(") : src.find("function frShiftWeekToCurrent(")]
        self.assertIn("frSourceOwnsIncomingOpening", push)

    def test_shop_control_example_week(self):
        area = "farea_shop"
        pk = "week:2026-10-15"
        sources = [
            {"finance_source_id": "main", "finance_area_id": area, "in_actual_balance": True},
            {"finance_source_id": "bank", "finance_area_id": area, "in_actual_balance": True},
            {"finance_source_id": "kkm", "finance_area_id": area, "in_actual_balance": False},
            {"finance_source_id": "anisa", "finance_area_id": area, "in_actual_balance": False},
            {"finance_source_id": "rita", "finance_area_id": area, "in_actual_balance": False},
            {"finance_source_id": "olya", "finance_area_id": area, "in_actual_balance": False},
            {"finance_source_id": "vik", "finance_area_id": area, "in_actual_balance": False},
        ]
        openings = [
            {"finance_area_id": area, "period_key": pk, "finance_source_id": "main", "value": 4613975.12},
            {"finance_area_id": area, "period_key": pk, "finance_source_id": "bank", "value": 497138.34},
            {"finance_area_id": area, "period_key": pk, "finance_source_id": "kkm", "value": 452064.38},
            {"finance_area_id": area, "period_key": pk, "finance_source_id": "anisa", "value": 901733.53},
            {"finance_area_id": area, "period_key": pk, "finance_source_id": "rita", "value": 211783.73},
            {"finance_area_id": area, "period_key": pk, "finance_source_id": "olya", "value": 1000.0},
            {"finance_area_id": area, "period_key": pk, "finance_source_id": "vik", "value": 104562.68},
        ]
        by_id = {s["finance_source_id"]: s for s in sources}
        self.assertEqual(week_incoming(by_id, openings, area, pk, "main"), 4613975.12)
        self.assertEqual(week_incoming(by_id, openings, area, pk, "bank"), 497138.34)
        for sid in ("kkm", "anisa", "rita", "olya", "vik"):
            self.assertEqual(week_incoming(by_id, openings, area, pk, sid), 0.0)
        self.assertEqual(area_week_opening(sources, openings, area, pk), 5111113.46)

    def test_territory_same_rule(self):
        area = "farea_territory"
        pk = "week:2026-10-15"
        sources = [
            {"finance_source_id": "t_main", "finance_area_id": area, "in_actual_balance": True},
            {"finance_source_id": "t_ctrl", "finance_area_id": area, "in_actual_balance": False},
        ]
        openings = [
            {"finance_area_id": area, "period_key": pk, "finance_source_id": "t_main", "value": 100000.0},
            {"finance_area_id": area, "period_key": pk, "finance_source_id": "t_ctrl", "value": 25000.0},
        ]
        by_id = {s["finance_source_id"]: s for s in sources}
        self.assertEqual(week_incoming(by_id, openings, area, pk, "t_main"), 100000.0)
        self.assertEqual(week_incoming(by_id, openings, area, pk, "t_ctrl"), 0.0)
        self.assertEqual(area_week_opening(sources, openings, area, pk), 100000.0)

    def test_carry_non_fact_is_zero(self):
        ctrl = {"finance_source_id": "kkm", "in_actual_balance": False}
        main = {"finance_source_id": "main", "in_actual_balance": True}
        self.assertEqual(carry_value(ctrl, 452064.38), 0.0)
        self.assertEqual(carry_value(main, 4613975.12), 4613975.12)


if __name__ == "__main__":
    unittest.main()
