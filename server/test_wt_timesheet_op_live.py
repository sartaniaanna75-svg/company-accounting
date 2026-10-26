# -*- coding: utf-8 -*-
"""
Живой прогон POST /api/state/op против локального сервера 127.0.0.1:8780.

Не требует PIN: создаёт временную сессию в worktime_test через тот же DATABASE_URL.
Запуск (из server, с уже выставленным DATABASE_URL локального теста):

  python test_wt_timesheet_op_live.py

Пропускается, если сервер недоступен или DATABASE_URL не указывает на worktime_test.
"""

from __future__ import annotations

import json
import os
import re
import sys
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

BASE = os.environ.get("WT_LIVE_BASE", "http://127.0.0.1:8780").rstrip("/")
ADMIN_ID = "test_admin_local"
SHOP_ID = "test_shop_local"
CELL_PATH = "workspaces.shop.timesheet.2026-10.e_live_probe.7"
CELL_VALUE = {"status": "Я", "hours": 8, "lateMinutes": 0, "live_probe": True}


def _url_is_local_test(url: str) -> bool:
    low = (url or "").strip().lower()
    if not low:
        return False
    if any(x in low for x in ("timeweb", "twcstorage", "amazonaws")):
        return False
    return bool(
        re.match(
            r"^postgres(?:ql)?://worktime_tester:[^@]+@(127\.0\.0\.1|localhost):5433/worktime_test(\?.*)?$",
            low,
        )
    )


def _http_json(method: str, path: str, body=None, cookie: str | None = None):
    data = None
    headers = {"Content-Type": "application/json"}
    if cookie:
        headers["Cookie"] = cookie
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8")
            parsed = json.loads(raw) if raw else {}
            return resp.status, parsed, resp.headers
    except urllib.error.HTTPError as err:
        raw = err.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = {"raw": raw[:300]}
        return err.code, parsed, err.headers


def _server_up() -> bool:
    try:
        code, data, _ = _http_json("GET", "/api/health")
        return code == 200 and data.get("ok") is True
    except Exception:
        return False


@unittest.skipUnless(_server_up(), "local server 127.0.0.1:8780 not reachable")
class WtTimesheetOpLiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = (os.environ.get("DATABASE_URL") or "").strip()
        if not _url_is_local_test(url):
            raise unittest.SkipTest(
                "DATABASE_URL must be worktime_tester@127.0.0.1:5433/worktime_test"
            )
        os.environ["WT_LOCAL_TEST"] = "1"
        os.environ.setdefault("WT_PIN_PEPPER", "unit-pepper")
        os.environ.setdefault("WT_SCRYPT_N", "16")
        import wt_api

        cls.wt_api = wt_api
        # Сессия без PIN: пишем token в app_sessions.
        raw, ttl = wt_api.create_session(ADMIN_ID)
        cls.cookie = "wt_session=" + raw
        cls._session_raw = raw

    @classmethod
    def tearDownClass(cls):
        try:
            import wt_api

            conn = wt_api.connect()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM app_sessions WHERE token_hash = %s",
                        (wt_api.token_hash(cls._session_raw),),
                    )
                conn.commit()
            finally:
                conn.close()
        except Exception:
            pass

    def test_01_health_ops_true(self):
        code, data, _ = _http_json("GET", "/api/health")
        self.assertEqual(code, 200)
        self.assertTrue(data.get("ok"))
        self.assertTrue(data.get("ops"))

    def test_02_post_op_saves_and_reads_back(self):
        st, body, _ = _http_json(
            "POST",
            "/api/state/op",
            {
                "idempotency_key": "live-pilot-" + datetime.now(timezone.utc).strftime("%H%M%S%f"),
                "op": {"path": CELL_PATH, "expected_v": 0, "value": CELL_VALUE},
            },
            cookie=self.cookie,
        )
        # expected_v может быть >0 если путь уже писали — тогда повторим с server_v.
        if st == 409 and body.get("error") == "conflict":
            st, body, _ = _http_json(
                "POST",
                "/api/state/op",
                {
                    "idempotency_key": "live-pilot-retry-"
                    + datetime.now(timezone.utc).strftime("%H%M%S%f"),
                    "op": {
                        "path": CELL_PATH,
                        "expected_v": int(body.get("server_v") or 0),
                        "value": CELL_VALUE,
                    },
                },
                cookie=self.cookie,
            )
        self.assertEqual(st, 200, body)
        self.assertTrue(body.get("ok"))
        self.assertEqual(body.get("path"), CELL_PATH)
        self.assertEqual(body.get("value"), CELL_VALUE)
        self.assertGreaterEqual(int(body.get("record_v") or 0), 1)

        # Чтение через GET /api/state
        st2, state, _ = _http_json("GET", "/api/state", cookie=self.cookie)
        self.assertEqual(st2, 200, state)
        payload = state.get("payload") or {}
        got = self.wt_api.get_path_value(payload, CELL_PATH)
        self.assertEqual(got, CELL_VALUE)
        meta = (payload.get("_wt_records") or {}).get(CELL_PATH) or {}
        self.assertGreaterEqual(int(meta.get("v") or 0), 1)

    def test_03_shop_user_independent_cell(self):
        raw, _ttl = self.wt_api.create_session(SHOP_ID)
        cookie = "wt_session=" + raw
        path = "workspaces.shop.timesheet.2026-10.e_live_probe.8"
        value = {"status": "В", "hours": "", "lateMinutes": 0, "live_probe": True}
        try:
            st, body, _ = _http_json(
                "POST",
                "/api/state/op",
                {
                    "idempotency_key": "live-shop-" + datetime.now(timezone.utc).strftime("%H%M%S%f"),
                    "op": {"path": path, "expected_v": 0, "value": value},
                },
                cookie=cookie,
            )
            if st == 409 and body.get("error") == "conflict":
                st, body, _ = _http_json(
                    "POST",
                    "/api/state/op",
                    {
                        "idempotency_key": "live-shop-retry-"
                        + datetime.now(timezone.utc).strftime("%H%M%S%f"),
                        "op": {
                            "path": path,
                            "expected_v": int(body.get("server_v") or 0),
                            "value": value,
                        },
                    },
                    cookie=cookie,
                )
            self.assertEqual(st, 200, body)
            self.assertEqual(body.get("value"), value)
            # Чужая ячейка админа не должна пропасть
            st2, state, _ = _http_json("GET", "/api/state", cookie=self.cookie)
            self.assertEqual(st2, 200)
            payload = state.get("payload") or {}
            self.assertEqual(self.wt_api.get_path_value(payload, CELL_PATH), CELL_VALUE)
            self.assertEqual(self.wt_api.get_path_value(payload, path), value)
        finally:
            try:
                conn = self.wt_api.connect()
                try:
                    with conn.cursor() as cur:
                        cur.execute(
                            "DELETE FROM app_sessions WHERE token_hash = %s",
                            (self.wt_api.token_hash(raw),),
                        )
                    conn.commit()
                finally:
                    conn.close()
            except Exception:
                pass


class WtTimesheetOpLiveUiGuardTests(unittest.TestCase):
    """Статика app.html: порядок save→refresh и блок incidental PUT."""

    @classmethod
    def setUpClass(cls):
        from pathlib import Path

        cls.src = (Path(__file__).resolve().parent.parent / "app.html").read_text(encoding="utf-8")

    def test_block_incidental_put_flag(self):
        self.assertIn("wtOpsBlockIncidentalPut", self.src)
        self.assertIn("wtOpsBlockIncidentalPut && wtOpsUiEnabled()", self.src)

    def test_save_before_refresh(self):
        self.assertIn("function finishTimesheetCellSave(", self.src)
        self.assertIn("wtTimesheetSaveErrorToast", self.src)


if __name__ == "__main__":
    if not _server_up():
        print("SKIP: server not up at", BASE, file=sys.stderr)
        raise SystemExit(0)
    unittest.main()
