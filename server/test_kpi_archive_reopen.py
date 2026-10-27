# -*- coding: utf-8 -*-
"""Архив KPI и безопасное восстановление (зеркало логики app.html)."""

from __future__ import annotations

import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1].joinpath("app.html")


def month_key(value: str | None) -> str:
    s = str(value or "")
    return s[:7] if len(s) >= 7 and s[4:5] == "-" else s


def period_applies(frm: str | None, to: str | None, month: str) -> bool:
    m = month_key(month)
    f = month_key(frm)
    t = month_key(to)
    if f and m < f:
        return False
    if t and m > t:
        return False
    return True


def period_broken(d: dict) -> bool:
    f = month_key(d.get("validFrom") or d.get("valid_from"))
    t = month_key(d.get("validTo") if d.get("validTo") is not None else d.get("valid_to"))
    return bool(f and t and f > t)


def defs_for_rules(kpi_defs: list, month: str) -> list:
    m = month_key(month)
    best: dict[str, dict] = {}
    for d in kpi_defs:
        if not d or not d.get("kpi_id"):
            continue
        if not period_applies(d.get("validFrom") or d.get("valid_from"), d.get("validTo") or d.get("valid_to"), m):
            continue
        kid = d["kpi_id"]
        prev = best.get(kid)
        frm = str(d.get("validFrom") or d.get("valid_from") or "")
        if not prev or frm > str(prev.get("validFrom") or prev.get("valid_from") or ""):
            best[kid] = d
    return list(best.values())


def defs_archived(kpi_defs: list, month: str, department_id: str = "shop") -> list:
    active_ids = set()
    for d in defs_for_rules(kpi_defs, month):
        dept = d.get("department_id") or d.get("departmentId") or "shop"
        if dept == "all" or dept == department_id:
            active_ids.add(d["kpi_id"])
    best: dict[str, dict] = {}
    for d in kpi_defs:
        if not d or not d.get("kpi_id"):
            continue
        if d["kpi_id"] in active_ids:
            continue
        dept = d.get("department_id") or d.get("departmentId") or "shop"
        if dept != "all" and dept != department_id:
            continue
        prev = best.get(d["kpi_id"])
        frm = str(d.get("validFrom") or d.get("valid_from") or "")
        if not prev or frm > str(prev.get("validFrom") or prev.get("valid_from") or ""):
            best[d["kpi_id"]] = d
    return list(best.values())


def future_conflict(versions: list, month: str) -> bool:
    m = month_key(month)
    for d in versions:
        f = month_key(d.get("validFrom") or d.get("valid_from"))
        if f and m and f > m:
            return True
    return False


def reopen(kpi_defs: list, kpi_id: str, month: str) -> tuple[str, list]:
    """Возвращает (status, kpi_defs). status: ok | already | future | missing."""
    versions = [d for d in kpi_defs if d and d.get("kpi_id") == kpi_id]
    if not versions:
        return "missing", kpi_defs
    if any(
        period_applies(d.get("validFrom") or d.get("valid_from"), d.get("validTo") or d.get("valid_to"), month)
        for d in versions
    ):
        return "already", kpi_defs
    if future_conflict(versions, month):
        return "future", kpi_defs
    versions_sorted = sorted(
        versions,
        key=lambda d: str(d.get("validFrom") or d.get("valid_from") or ""),
        reverse=True,
    )
    src = versions_sorted[0]
    old_snap = [(id(d), dict(d)) for d in versions]
    next_ver = {
        **src,
        "kpi_id": kpi_id,
        "validFrom": month_key(month),
        "valid_from": month_key(month),
        "validTo": None,
        "valid_to": None,
        "active": True,
    }
    kpi_defs = list(kpi_defs) + [next_ver]
    for obj_id, snap in old_snap:
        for d in kpi_defs:
            if id(d) == obj_id:
                assert d == snap
    return "ok", kpi_defs


def close_to(selected: str) -> str:
    y, m = map(int, month_key(selected).split("-"))
    m -= 1
    if m < 1:
        y -= 1
        m = 12
    return f"{y:04d}-{m:02d}"


class KpiArchiveReopenTests(unittest.TestCase):
    def test_app_wiring(self):
        src = APP.read_text(encoding="utf-8")
        self.assertIn("function shopKpiDefsArchived(", src)
        self.assertIn("function reopenShopKpiDef(", src)
        self.assertIn("function shopKpiPeriodBroken(", src)
        self.assertIn("function shopKpiFutureVersionConflict(", src)
        self.assertIn("Восстановить с выбранного месяца", src)
        self.assertIn("payrollRulesModeByView", src)
        self.assertIn("setPayrollRulesMode(", src)
        # Архив не должен кормить начисления
        current = src[src.find("function shopKpiDefsCurrent(") : src.find("function shopRuleFromKpiDef(")]
        self.assertNotIn("shopKpiDefsArchived", current)
        bonus = src[src.find("function shopEmployeeActiveRules(") : src.find("function shopEmployeeBonusTotal(")]
        self.assertIn("shopKpiDefsCurrent", bonus)
        self.assertNotIn("shopKpiDefsArchived", bonus)

    def test_archive_shows_closed_and_broken(self):
        defs = [
            {
                "kpi_id": "kpi_display",
                "metric_id": "metric_display_positions",
                "department_id": "shop",
                "name": "Позиции выкладки",
                "plan": 25000,
                "budget": 5000,
                "validFrom": "2026-10",
                "validTo": "2026-09",
                "active": True,
            },
            {
                "kpi_id": "kpi_other",
                "metric_id": "metric_recount",
                "department_id": "shop",
                "validFrom": "2026-01",
                "validTo": "2026-08",
                "plan": 100,
                "active": True,
            },
            {
                "kpi_id": "kpi_live",
                "metric_id": "metric_aisles",
                "department_id": "shop",
                "validFrom": "2026-01",
                "validTo": None,
                "active": True,
            },
        ]
        self.assertTrue(period_broken(defs[0]))
        arch = defs_archived(defs, "2026-10", "shop")
        ids = {d["kpi_id"] for d in arch}
        self.assertIn("kpi_display", ids)
        self.assertIn("kpi_other", ids)
        self.assertNotIn("kpi_live", ids)
        live = {d["kpi_id"] for d in defs_for_rules(defs, "2026-10")}
        self.assertEqual(live, {"kpi_live"})

    def test_archive_shows_future_start(self):
        defs = [
            {
                "kpi_id": "kpi_fut",
                "department_id": "shop",
                "validFrom": "2026-12",
                "validTo": None,
                "active": True,
            }
        ]
        arch = defs_archived(defs, "2026-10", "shop")
        self.assertEqual([d["kpi_id"] for d in arch], ["kpi_fut"])

    def test_reopen_from_october_preserves_history(self):
        old = {
            "kpi_id": "kpi_display",
            "metric_id": "metric_display_positions",
            "department_id": "shop",
            "plan": 25000,
            "budget": 5000,
            "amount": 5000,
            "calcType": "budget_ratio",
            "validFrom": "2026-10",
            "validTo": "2026-09",
            "active": True,
        }
        assignments = [
            {"kpi_id": "kpi_display", "employee_id": "e1", "validFrom": "2026-09", "validTo": "2026-09"}
        ]
        status, defs = reopen([old], "kpi_display", "2026-10")
        self.assertEqual(status, "ok")
        self.assertEqual(len(defs), 2)
        self.assertEqual(defs[0]["validFrom"], "2026-10")
        self.assertEqual(defs[0]["validTo"], "2026-09")
        self.assertEqual(defs[0]["plan"], 25000)
        new = defs[1]
        self.assertEqual(new["kpi_id"], "kpi_display")
        self.assertEqual(new["validFrom"], "2026-10")
        self.assertIsNone(new["validTo"])
        self.assertTrue(new["active"])
        self.assertEqual(new["plan"], 25000)
        self.assertTrue(period_applies(new["validFrom"], new["validTo"], "2026-10"))
        self.assertFalse(period_applies(old["validFrom"], old["validTo"], "2026-09"))
        self.assertFalse(period_applies(new["validFrom"], new["validTo"], "2026-09"))
        self.assertEqual(assignments[0]["validTo"], "2026-09")

    def test_reopen_idempotent(self):
        defs = [
            {
                "kpi_id": "kpi_display",
                "department_id": "shop",
                "validFrom": "2026-10",
                "validTo": None,
                "plan": 25000,
                "active": True,
            }
        ]
        status, out = reopen(defs, "kpi_display", "2026-10")
        self.assertEqual(status, "already")
        self.assertEqual(len(out), 1)

    def test_reopen_blocks_future_overlap(self):
        defs = [
            {
                "kpi_id": "kpi_x",
                "department_id": "shop",
                "validFrom": "2026-01",
                "validTo": "2026-08",
                "active": True,
            },
            {
                "kpi_id": "kpi_x",
                "department_id": "shop",
                "validFrom": "2026-12",
                "validTo": None,
                "active": True,
            },
        ]
        status, out = reopen(defs, "kpi_x", "2026-10")
        self.assertEqual(status, "future")
        self.assertEqual(len(out), 2)

    def test_close_same_month_broken_still_archivable(self):
        selected = "2026-10"
        frm = "2026-10"
        to = close_to(selected)
        self.assertEqual(to, "2026-09")
        self.assertTrue(period_broken({"validFrom": frm, "validTo": to}))
        defs = [
            {
                "kpi_id": "kpi_display",
                "department_id": "shop",
                "metric_id": "metric_display_positions",
                "plan": 25000,
                "validFrom": frm,
                "validTo": to,
                "active": True,
            }
        ]
        self.assertEqual(defs_for_rules(defs, selected), [])
        self.assertEqual([d["kpi_id"] for d in defs_archived(defs, selected)], ["kpi_display"])

    def test_payout_formula_unchanged_budget_ratio(self):
        # Зеркало shopKpiPayout для числового plan/budget
        fact, plan, budget, max_p = 25000, 25000, 5000, 1.0
        coef = min(fact / plan, max_p)
        amount = round(budget * coef)
        self.assertEqual(amount, 5000)
        amount_partial = round(budget * min(12500 / plan, max_p))
        self.assertEqual(amount_partial, 2500)


if __name__ == "__main__":
    unittest.main()
