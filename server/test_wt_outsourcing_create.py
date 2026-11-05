# -*- coding: utf-8 -*-
"""Unit-тесты аутсорсинга: create-record и изоляция от employeeIds (без БД)."""
from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import wt_api  # noqa: E402


class OutsourcingCreateRecordTests(unittest.TestCase):
    def test_build_sets_flag_rate_terms(self):
        rec = wt_api.build_created_employee_record(
            "T-O-99",
            {
                "name": "Финансист",
                "outsourcing": True,
                "outsourcingFixedPay": 100000,
                "rate": 100000,
                "terms": {"2026-11": {"rate": 100000, "taxiDaily": 0}},
            },
            "office",
            "Офис",
        )
        self.assertTrue(rec["outsourcing"] is True)
        self.assertEqual(rec["rate"], 100000)
        self.assertEqual(rec["outsourcingFixedPay"], 100000)
        self.assertEqual(rec["terms"]["2026-11"]["rate"], 100000)

    def test_build_regular_employee_no_outsourcing(self):
        rec = wt_api.build_created_employee_record(
            "T-O-1",
            {"name": "Обычный", "rate": 2000},
            "office",
            "Офис",
        )
        self.assertFalse(rec.get("outsourcing") is True)
        self.assertEqual(rec["rate"], 0)
        self.assertEqual(rec.get("outsourcingFixedPay"), 0)


if __name__ == "__main__":
    unittest.main()
