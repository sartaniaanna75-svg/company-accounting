# -*- coding: utf-8 -*-
"""Единый factDept = home для метрик Территории (вариант A)."""

from __future__ import annotations

import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1].joinpath("app.html")


def fact_dept(home, fallback="drivers", generic=("office", "warehouse", "drivers")):
    if home in generic:
        return home
    return fallback


def read_fact(stores, dept, emp, day, metric_id):
    leaf = (((stores.get(dept) or {}).get("facts") or {}).get("2026-10") or {}).get(emp) or {}
    day_rec = leaf.get(str(day)) or {}
    return float(day_rec.get(metric_id) or 0)


def diagnose_split(stores, home, host, emp, day, metric_id):
    if home == host:
        return None
    hv = read_fact(stores, home, emp, day, metric_id)
    xv = read_fact(stores, host, emp, day, metric_id)
    if abs(hv) < 1e-9 and abs(xv) < 1e-9:
        return None
    if abs(hv - xv) < 1e-9:
        return None
    return {"home": home, "host": host, "homeValue": hv, "hostValue": xv, "conflict": True}


class MetricFactDeptLogicTests(unittest.TestCase):
    def test_app_html_wires_fact_dept_fn(self):
        src = APP.read_text(encoding="utf-8")
        self.assertIn("function diagnoseMetricFactDeptSplit(", src)
        dwork = src[src.find("function renderDeptWorkMetrics(") : src.find("function fillDeptWorkTable(")]
        self.assertIn("metricFactDeptForEmployee(employeeId, dept)", dwork)

    def test_sidorova_warehouse_home_opt_screens_same_store(self):
        """Склад + назначение ОПТ: хаб и Торговые читают warehouse."""
        emp = "e_sidorova"
        stores = {
            "warehouse": {"facts": {"2026-10": {emp: {"5": {"m_loaded": 12, "m_processed": 8, "m_packed": 5}}}}},
            "drivers": {"facts": {"2026-10": {}}},
        }
        home = "warehouse"
        hub_dept = fact_dept(home, "drivers")
        work_dept = fact_dept(home, "drivers")
        self.assertEqual(hub_dept, "warehouse")
        self.assertEqual(work_dept, hub_dept)
        self.assertEqual(read_fact(stores, hub_dept, emp, 5, "m_loaded"), 12)
        self.assertEqual(read_fact(stores, work_dept, emp, 5, "m_loaded"), 12)
        self.assertEqual(read_fact(stores, "drivers", emp, 5, "m_loaded"), 0)

    def test_warehouse_and_sales_metrics_do_not_mix(self):
        emp = "e_wh"
        stores = {
            "warehouse": {
                "facts": {
                    "2026-10": {
                        emp: {
                            "5": {"m_wh_pick": 3, "m_opt_loaded": 10},
                        }
                    }
                }
            }
        }
        dept = fact_dept("warehouse", "drivers")
        self.assertEqual(read_fact(stores, dept, emp, 5, "m_wh_pick"), 3)
        self.assertEqual(read_fact(stores, dept, emp, 5, "m_opt_loaded"), 10)
        self.assertNotEqual(
            read_fact(stores, dept, emp, 5, "m_wh_pick"),
            read_fact(stores, dept, emp, 5, "m_opt_loaded"),
        )

    def test_office_cross_assign_uses_office_home(self):
        self.assertEqual(fact_dept("office", "drivers"), "office")
        self.assertEqual(fact_dept("office", "warehouse"), "office")

    def test_native_drivers_employee_unchanged(self):
        self.assertEqual(fact_dept("drivers", "drivers"), "drivers")

    def test_reassign_keeps_home_facts(self):
        emp = "e_x"
        stores = {"warehouse": {"facts": {"2026-10": {emp: {"1": {"m_opt": 7}}}}}}
        # Снятие/повтор назначения меняет только колонку; home-facts на месте.
        self.assertEqual(read_fact(stores, fact_dept("warehouse"), emp, 1, "m_opt"), 7)

    def test_dual_store_conflict_detected_not_merged(self):
        emp = "e_dual"
        stores = {
            "warehouse": {"facts": {"2026-10": {emp: {"3": {"m_opt": 10}}}}},
            "drivers": {"facts": {"2026-10": {emp: {"3": {"m_opt": 99}}}}},
        }
        before_home = read_fact(stores, "warehouse", emp, 3, "m_opt")
        before_host = read_fact(stores, "drivers", emp, 3, "m_opt")
        split = diagnose_split(stores, "warehouse", "drivers", emp, 3, "m_opt")
        self.assertIsNotNone(split)
        self.assertTrue(split["conflict"])
        self.assertEqual(split["homeValue"], 10)
        self.assertEqual(split["hostValue"], 99)
        # Данные не трогаем.
        self.assertEqual(read_fact(stores, "warehouse", emp, 3, "m_opt"), before_home)
        self.assertEqual(read_fact(stores, "drivers", emp, 3, "m_opt"), before_host)

    def test_kpi_home_path_matches_unified_fact_dept(self):
        """KPI territory читает home — совпадает с единым factDept UI."""
        emp = "e_kpi"
        stores = {"warehouse": {"facts": {"2026-10": {emp: {"2": {"m_opt": 4}}}}}}
        kpi_dept = fact_dept("warehouse")  # как deptMetricPersonalMonthTotal(home,…)
        ui_dept = fact_dept("warehouse", "drivers")
        self.assertEqual(kpi_dept, ui_dept)
        self.assertEqual(read_fact(stores, kpi_dept, emp, 2, "m_opt"), 4)


if __name__ == "__main__":
    unittest.main()
