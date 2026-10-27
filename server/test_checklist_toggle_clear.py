# -*- coding: utf-8 -*-
"""Логика toggle-clear чек-листов (зеркало setShop/setDeptMetricCheck)."""

from __future__ import annotations

import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1].joinpath("app.html")


def apply_check(rec, mark_key, mark):
    next_mark = "fail" if mark == "fail" else ("done" if mark == "done" else None)
    cur = rec.get(mark_key)
    if next_mark and cur == next_mark:
        rec[mark_key] = None  # metricMarkCleared
    elif next_mark:
        rec[mark_key] = next_mark
    else:
        rec[mark_key] = None
    return (rec[mark_key] if rec.get(mark_key) in ("done", "fail") else None)


def check_state(rec, mark_key):
    m = rec.get(mark_key)
    return m if m in ("done", "fail") else ""


class ChecklistToggleClearTests(unittest.TestCase):
    def test_app_wiring(self):
        src = APP.read_text(encoding="utf-8")
        self.assertIn("metricMarkCleared(rec, markKey)", src)
        dept = src[src.find("function setDeptMetricCheck(") : src.find("function deptMetricInputHtml(")]
        self.assertIn("wtSaveMetricValue(path, markVal)", dept)

    def test_done_toggle_to_empty(self):
        rec = {}
        self.assertEqual(apply_check(rec, "mMark", "done"), "done")
        self.assertEqual(check_state(rec, "mMark"), "done")
        self.assertIsNone(apply_check(rec, "mMark", "done"))
        self.assertEqual(check_state(rec, "mMark"), "")

    def test_fail_toggle_to_empty(self):
        rec = {}
        self.assertEqual(apply_check(rec, "mMark", "fail"), "fail")
        self.assertIsNone(apply_check(rec, "mMark", "fail"))
        self.assertEqual(check_state(rec, "mMark"), "")

    def test_switch_done_to_fail_and_back(self):
        rec = {}
        apply_check(rec, "mMark", "done")
        self.assertEqual(apply_check(rec, "mMark", "fail"), "fail")
        self.assertEqual(apply_check(rec, "mMark", "done"), "done")

    def test_empty_not_counted_as_done_or_fail(self):
        rec = {"mMark": None}
        self.assertEqual(check_state(rec, "mMark"), "")
        done = fail = nodata = 0
        for state in ("done", "fail", ""):
            if state == "done":
                done += 1
            elif state == "fail":
                fail += 1
            else:
                nodata += 1
        # После clear: как пусто в KPI stats
        state = check_state(rec, "mMark")
        if state == "done":
            done += 1
        elif state == "fail":
            fail += 1
        else:
            nodata += 1
        self.assertEqual(done, 1)
        self.assertEqual(fail, 1)
        self.assertEqual(nodata, 2)

    def test_clear_sends_null_not_absent_only(self):
        rec = {"mMark": "done"}
        mark_val = apply_check(rec, "mMark", "done")
        self.assertIsNone(mark_val)
        self.assertTrue("mMark" in rec)  # sentinel null остаётся локально до overlay
        self.assertIsNone(rec["mMark"])


if __name__ == "__main__":
    unittest.main()
