# -*- coding: utf-8 -*-
"""Этап 14: soft-refresh — version endpoint, merge без PUT, нагрузка poll."""

from __future__ import annotations

import json
import os
import re
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import wt_api
from test_wt_api import SAMPLE_APP, FakeConn, SECRET, call, cookie_from

APP_HTML = Path(__file__).resolve().parent.parent / "app.html"


class WtSoftRefreshApiTests(unittest.TestCase):
    def setUp(self):
        self._env_backup = dict(os.environ)
        os.environ["DATABASE_URL"] = "postgresql://worktime:" + SECRET + "@127.0.0.1:5432/worktime"
        os.environ["WT_PIN_PEPPER"] = "unit-pepper"
        os.environ["WT_SCRYPT_N"] = "16"
        os.environ["WT_OPS_ENABLED"] = "1"
        os.environ.pop("WT_API_TOKEN", None)
        wt_api.LOGIN_FAILS.clear()
        self.store = {
            "payload": json.loads(json.dumps(SAMPLE_APP)),
            "version": 5,
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

    def test_version_endpoint_light(self):
        cookie = self.login()
        st, data, *_ = call("GET", "/api/state/version", cookie=cookie)
        self.assertTrue(st.startswith("200"), st)
        # login может bump'нуть global version — сверяем со store, не с setUp.
        self.assertEqual(int(data.get("version")), int(self.store["version"]))
        self.assertNotIn("payload", data)
        self.assertIn("updated_at", data)

    def test_version_requires_auth(self):
        st, data, *_ = call("GET", "/api/state/version")
        self.assertTrue(st.startswith("401"), st)

    def test_version_bumps_after_op(self):
        cookie = self.login()
        st0, d0, *_ = call("GET", "/api/state/version", cookie=cookie)
        v0 = int(d0["version"])
        self.assertEqual(v0, int(self.store["version"]))
        st, *_ = call(
            "POST",
            "/api/state/op",
            {
                "op": {
                    "path": "workspaces.shop.timesheet.2026-10.e_shop.9",
                    "expected_v": 0,
                    "value": {"status": "Я", "hours": 8, "lateMinutes": 0},
                }
            },
            cookie=cookie,
        )
        self.assertTrue(st.startswith("200"), st)
        st1, d1, *_ = call("GET", "/api/state/version", cookie=cookie)
        self.assertGreater(int(d1["version"]), v0)

    def test_staff_sees_version(self):
        cookie = self.login("usr_001", "1111")
        st, data, *_ = call("GET", "/api/state/version", cookie=cookie)
        self.assertTrue(st.startswith("200"), st)
        self.assertEqual(int(data.get("version")), int(self.store["version"]))

    def test_parallel_version_polls_50(self):
        """50 параллельных GET /api/state/version — без ошибок и без payload."""
        cookie = self.login()
        results = []
        lock = threading.Lock()

        def job():
            st, data, *_ = call("GET", "/api/state/version", cookie=cookie)
            with lock:
                results.append((st, data))

        threads = [threading.Thread(target=job) for _ in range(50)]
        t0 = time.perf_counter()
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        elapsed = time.perf_counter() - t0
        self.assertEqual(len(results), 50)
        self.assertTrue(all(r[0].startswith("200") for r in results), results[:3])
        self.assertTrue(all("payload" not in r[1] for r in results))
        # Локальный FakeConn: уложиться в разумное время.
        self.assertLess(elapsed, 5.0, elapsed)


class WtSoftRefreshUiWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = APP_HTML.read_text(encoding="utf-8")

    def test_soft_poll_helpers(self):
        self.assertIn("function wtSoftPollStart(", self.src)
        self.assertIn("function wtSoftPollStop(", self.src)
        self.assertIn("function wtSoftApplyServerState(", self.src)
        self.assertIn("WT_SOFT_POLL_MS = 3000", self.src)
        self.assertIn('/api/state/version', self.src)
        self.assertIn("function wtSoftMergePreferServer(", self.src)
        self.assertIn("wtSoftDirtyPaths", self.src)
        self.assertIn("visibilitychange", self.src)
        self.assertIn("wtSoftOnVisibility", self.src)

    def test_soft_poll_no_saveapp(self):
        start = self.src.find("function wtSoftPollTick(")
        self.assertGreater(start, 0)
        block = self.src[start : self.src.find("\nfunction wtSoftOnVisibility(", start)]
        self.assertNotIn("saveApp(", block)
        self.assertNotIn('method: "PUT"', block)
        self.assertNotIn("putServerStateOnce(", block)

    def test_start_stop_hooks(self):
        # openApp starts, leaveWorkspace stops
        oa = self.src.find("function openApp(")
        oa_block = self.src[oa : self.src.find("\nfunction saveSession(", oa)]
        self.assertIn("wtSoftPollStart(", oa_block)
        lw = self.src.find("function leaveWorkspace(")
        lw_block = self.src[lw : self.src.find("\nfunction logout(", lw)]
        self.assertIn("wtSoftPollStop(", lw_block)

    def test_skips_render_while_editing(self):
        self.assertIn("function wtTimesheetHasActiveEdit(", self.src)
        self.assertIn("function wtMetricsHasActiveEdit(", self.src)
        self.assertIn("function wtSoftHasActiveEdit(", self.src)
        self.assertIn("wtSoftRenderPending", self.src)
        self.assertIn("wtSoftFlushPendingRender(", self.src)

    def test_soft_refresh_renders_current_section(self):
        start = self.src.find("function wtSoftRefreshUiAfterMerge(")
        self.assertGreater(start, 0)
        block = self.src[start : self.src.find("\nfunction wtSoftFlushPendingRender(", start)]
        self.assertIn("refreshAll(", block)
        self.assertIn("wtSoftApplying", block)
        self.assertIn("wtSoftAppliedVersion", block)

    def test_soft_poll_skips_during_put(self):
        start = self.src.find("function wtSoftPollTick(")
        self.assertGreater(start, 0)
        block = self.src[start : self.src.find("\nfunction wtSoftOnVisibility(", start)]
        self.assertIn("wtPutInFlight", block)

    def test_soft_apply_clears_put_pause(self):
        start = self.src.find("function wtSoftApplyServerState(")
        self.assertGreater(start, 0)
        block = self.src[start : self.src.find("\nfunction wtSoftRefreshUiAfterMerge(", start)]
        self.assertIn("wtPutPaused = false", block)
        self.assertIn("wtSoftMergePreferServer(", block)
        self.assertIn("wtLastLoadedPayload = wtCloneJson(serverAdopted)", block)

    def test_visibility_force_poll(self):
        self.assertIn("wtSoftPollTick(true)", self.src)
        start = self.src.find("function wtSoftPollTick(")
        block = self.src[start : self.src.find("\nfunction wtSoftOnVisibility(", start)]
        self.assertIn("document.hidden", block)
        self.assertIn("force", block)

    def test_dirty_helpers_wired(self):
        self.assertIn("function wtSoftMarkDirty(", self.src)
        self.assertIn("function wtSoftClearDirty(", self.src)
        self.assertIn("wtSoftMarkDirty(path)", self.src)

    def test_rejects_stale_soft_apply(self):
        start = self.src.find("function wtSoftApplyServerState(")
        self.assertGreater(start, 0)
        block = self.src[start : self.src.find("\nfunction wtSoftRefreshUiAfterMerge(", start)]
        self.assertIn("incomingV < knownV", block)
        self.assertIn("return false", block)

    def test_op_success_bumps_soft_applied_version(self):
        start = self.src.find("function wtSaveRecordOp(")
        self.assertGreater(start, 0)
        block = self.src[start : self.src.find("\nfunction wtResolveRecordConflict(", start)]
        self.assertIn("wtSoftAppliedVersion", block)

    def test_fact_backed_mirror_category_ops(self):
        start = self.src.find("function setShopMetricValue(")
        block = self.src[start : self.src.find("\nfunction changeShopMetricCategory(", start)]
        self.assertIn("mirrorPath", block)
        self.assertIn("wtShopCategoryLeafPath(", block)
        self.assertIn("wtShopFactsLeafPath(", block)


class WtSoftMergePreferServerLogicTests(unittest.TestCase):
    """Этап 18: семантика soft-merge (порт правила из app.html)."""

    def merge(self, server, draft, dirty, path=""):
        dirty = dirty or {}
        if path and dirty.get(path):
            return json.loads(json.dumps(draft if draft is not None else server))
        if isinstance(server, list) or isinstance(draft, list):
            return json.loads(json.dumps(server))
        if isinstance(server, dict) or isinstance(draft, dict):
            s_obj = server if isinstance(server, dict) else {}
            d_obj = draft if isinstance(draft, dict) else {}
            keys = set(s_obj)
            for k in d_obj:
                child = f"{path}.{k}" if path else k
                if dirty.get(child) or any(
                    dp.startswith(child + ".") for dp in dirty if dirty.get(dp)
                ):
                    keys.add(k)
            out = {}
            for k in keys:
                child = f"{path}.{k}" if path else k
                if k in s_obj:
                    out[k] = self.merge(s_obj.get(k), d_obj.get(k), dirty, child)
                elif dirty.get(child) or any(
                    dp.startswith(child + ".") for dp in dirty if dirty.get(dp)
                ):
                    out[k] = json.loads(json.dumps(d_obj.get(k)))
            return out
        return json.loads(json.dumps(server))

    def test_stale_zero_yields_to_server_without_dirty(self):
        server = {"shopMetrics": {"pickers": {"2026-10": {"V-1": {"9": {"display": 12}}}}}}
        draft = {"shopMetrics": {"pickers": {"2026-10": {"V-1": {"9": {"display": 0}}}}}}
        merged = self.merge(server, draft, {})
        self.assertEqual(merged["shopMetrics"]["pickers"]["2026-10"]["V-1"]["9"]["display"], 12)

    def test_dirty_leaf_kept(self):
        server = {"shopMetrics": {"pickers": {"2026-10": {"V-1": {"9": {"display": 12}}}}}}
        draft = {"shopMetrics": {"pickers": {"2026-10": {"V-1": {"9": {"display": 99}}}}}}
        path = "shopMetrics.pickers.2026-10.V-1.9.display"
        merged = self.merge(server, draft, {path: True})
        self.assertEqual(merged["shopMetrics"]["pickers"]["2026-10"]["V-1"]["9"]["display"], 99)

    def test_neighbor_leaf_takes_server(self):
        server = {
            "shopMetrics": {
                "pickers": {"2026-10": {"V-1": {"9": {"display": 12, "invoices": 7}}}}
            }
        }
        draft = {
            "shopMetrics": {
                "pickers": {"2026-10": {"V-1": {"9": {"display": 99, "invoices": 1}}}}
            }
        }
        path = "shopMetrics.pickers.2026-10.V-1.9.display"
        merged = self.merge(server, draft, {path: True})
        day = merged["shopMetrics"]["pickers"]["2026-10"]["V-1"]["9"]
        self.assertEqual(day["display"], 99)
        self.assertEqual(day["invoices"], 7)


if __name__ == "__main__":
    unittest.main()
