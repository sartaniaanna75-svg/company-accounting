# -*- coding: utf-8 -*-
"""Автотесты API хранилища без PostgreSQL и без Timeweb."""

from __future__ import annotations

import json
import os
import unittest
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import wt_api

SECRET = "super-secret-db-password-xyz"
SAMPLE_APP = {
    "workspaces": [{"id": "ws1", "code": "shop", "name": "Магазин Володарского"}],
    "users": [{"user_id": "usr_001", "display_name": "ВИКУЛИШНА"}],
    "salary": {},
}


class FakeCursor:
    def __init__(self, store):
        self.store = store
        self._row = None
        self.fail_next = False

    def execute(self, sql, params=None):
        if self.store.get("fail"):
            raise RuntimeError(
                "could not connect to server password=" + SECRET
            )
        text = " ".join(str(sql).split())
        if "SELECT 1" in text:
            self._row = (1,)
            return
        if text.startswith("INSERT INTO app_state"):
            self._row = None
            return
        if text.startswith("UPDATE app_state"):
            payload_raw, updated_by, _sid, expected = params
            if int(self.store["version"]) != int(expected):
                self._row = None
                return
            payload = json.loads(payload_raw) if isinstance(payload_raw, str) else payload_raw
            self.store["payload"] = payload
            self.store["version"] = int(self.store["version"]) + 1
            self.store["updated_by"] = updated_by
            self.store["updated_at"] = datetime.now(timezone.utc)
            self._row = (
                self.store["payload"],
                self.store["version"],
                self.store["updated_at"],
                self.store["updated_by"],
            )
            return
        if "SELECT payload, version" in text:
            self._row = (
                self.store["payload"],
                self.store["version"],
                self.store["updated_at"],
                self.store["updated_by"],
            )
            return
        self._row = None

    def fetchone(self):
        return self._row

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeConn:
    def __init__(self, store):
        self.store = store

    def cursor(self):
        return FakeCursor(self.store)

    def commit(self):
        self.store["committed"] = True

    def rollback(self):
        self.store["rolled_back"] = True

    def close(self):
        self.store["closed"] = True


def call(method, path, body=None):
    raw = b""
    if body is not None:
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8") if not isinstance(body, (bytes, bytearray)) else body
    captured = {}

    def start_response(status, headers):
        captured["status"] = status
        captured["headers"] = headers

    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "CONTENT_LENGTH": str(len(raw)),
        "wsgi.input": BytesIO(raw),
    }
    result = b"".join(wt_api.application(environ, start_response))
    data = json.loads(result.decode("utf-8")) if result else None
    return captured["status"], data, result.decode("utf-8")


class StoreApiTests(unittest.TestCase):
    def setUp(self):
        self._env_backup = dict(os.environ)
        os.environ["DATABASE_URL"] = "postgresql://worktime:" + SECRET + "@127.0.0.1:5432/worktime"
        self.store = {
            "payload": {},
            "version": 0,
            "updated_at": None,
            "updated_by": "",
            "fail": False,
        }
        self._patch = patch.object(wt_api, "connect", side_effect=lambda: FakeConn(self.store))
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        os.environ.clear()
        os.environ.update(self._env_backup)

    def test_health_ok(self):
        status, data, _ = call("GET", "/api/health")
        self.assertTrue(status.startswith("200"))
        self.assertEqual(data, {"ok": True, "db": True})

    def test_get_empty_state(self):
        status, data, _ = call("GET", "/api/state")
        self.assertTrue(status.startswith("200"))
        self.assertEqual(data["version"], 0)
        self.assertEqual(data["payload"], {})

    def test_put_and_get_appdata(self):
        status, data, _ = call("PUT", "/api/state", {
            "version": 0,
            "payload": SAMPLE_APP,
            "updated_by": "usr_001",
        })
        self.assertTrue(status.startswith("200"))
        self.assertEqual(data["version"], 1)
        self.assertEqual(data["payload"]["users"][0]["user_id"], "usr_001")
        status2, got, _ = call("GET", "/api/state")
        self.assertTrue(status2.startswith("200"))
        self.assertEqual(got["version"], 1)
        self.assertEqual(got["payload"]["workspaces"][0]["code"], "shop")

    def test_stale_version_does_not_overwrite(self):
        call("PUT", "/api/state", {"version": 0, "payload": SAMPLE_APP, "updated_by": "A"})
        stale = {
            "workspaces": [{"id": "ws1", "code": "office", "name": "Офис"}],
            "users": [],
        }
        status, data, _ = call("PUT", "/api/state", {
            "version": 0,
            "payload": stale,
            "updated_by": "B",
        })
        self.assertTrue(status.startswith("409"))
        self.assertEqual(data["error"], "conflict")
        self.assertEqual(data["version"], 1)
        self.assertEqual(data["payload"]["users"][0]["user_id"], "usr_001")
        _, current, _ = call("GET", "/api/state")
        self.assertEqual(current["payload"]["workspaces"][0]["code"], "shop")
        self.assertNotEqual(current["payload"]["workspaces"][0]["code"], "office")

    def test_second_writer_succeeds_with_fresh_version(self):
        call("PUT", "/api/state", {"version": 0, "payload": SAMPLE_APP, "updated_by": "A"})
        next_payload = dict(SAMPLE_APP)
        next_payload = {
            "workspaces": SAMPLE_APP["workspaces"],
            "users": SAMPLE_APP["users"] + [{"user_id": "user_admin", "display_name": "Анна"}],
        }
        status, data, _ = call("PUT", "/api/state", {
            "version": 1,
            "payload": next_payload,
            "updated_by": "B",
        })
        self.assertTrue(status.startswith("200"))
        self.assertEqual(data["version"], 2)
        self.assertEqual(len(data["payload"]["users"]), 2)

    def test_invalid_json(self):
        status, data, _ = call("PUT", "/api/state", b"{not-json")
        self.assertTrue(status.startswith("400"))
        self.assertEqual(data["error"], "invalid json")

    def test_missing_version(self):
        status, data, _ = call("PUT", "/api/state", {"payload": SAMPLE_APP})
        self.assertTrue(status.startswith("400"))
        self.assertEqual(data["error"], "version is required")

    def test_unknown_path(self):
        status, data, _ = call("GET", "/api/secret")
        self.assertTrue(status.startswith("404"))
        self.assertEqual(data["error"], "not found")

    def test_db_error_does_not_leak_password(self):
        self.store["fail"] = True
        for method, path, body in (
            ("GET", "/api/health", None),
            ("GET", "/api/state", None),
            ("PUT", "/api/state", {"version": 0, "payload": SAMPLE_APP}),
        ):
            status, data, raw = call(method, path, body)
            self.assertTrue(status.startswith("503"), path)
            self.assertNotIn(SECRET, raw)
            self.assertNotIn("password=", raw.lower())
            blob = json.dumps(data, ensure_ascii=False)
            self.assertNotIn(SECRET, blob)
            self.assertEqual(data.get("error"), "database unavailable")

    def test_connect_failure_no_secrets(self):
        self._patch.stop()
        with patch.object(wt_api, "connect", side_effect=RuntimeError("dsn password=" + SECRET)):
            status, data, raw = call("GET", "/api/state")
        self._patch.start()
        self.assertTrue(status.startswith("503"))
        self.assertNotIn(SECRET, raw)
        self.assertEqual(data["error"], "database unavailable")

    def test_dsn_uses_env_not_hardcoded_password(self):
        dsn = wt_api.dsn()
        self.assertIn(SECRET, dsn)
        src = Path(__file__).with_name("wt_api.py").read_text(encoding="utf-8")
        self.assertNotIn(SECRET, src)
        self.assertNotIn("CHANGE_ME", src)
        self.assertNotRegex(src, r"postgresql://[^:]+:[^@]+@")


class UnconfiguredTests(unittest.TestCase):
    def setUp(self):
        self._env_backup = dict(os.environ)
        for key in ("DATABASE_URL", "PGPASSWORD", "PGUSER", "PGHOST", "PGDATABASE"):
            os.environ.pop(key, None)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env_backup)

    def test_health_without_env(self):
        status, data, _ = call("GET", "/api/health")
        self.assertTrue(status.startswith("503"))
        self.assertEqual(data["ok"], False)
        self.assertEqual(data["db"], False)

    def test_state_without_env(self):
        status, data, raw = call("GET", "/api/state")
        self.assertTrue(status.startswith("503"))
        self.assertEqual(data["error"], "database unavailable")
        self.assertNotIn("password", raw.lower())


class SourceAndBindTests(unittest.TestCase):
    def test_default_bind_is_localhost(self):
        os.environ.pop("WT_BIND", None)
        self.assertEqual(wt_api.bind_host(), "127.0.0.1")

    def test_schema_has_version_column(self):
        sql = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
        self.assertIn("version", sql)
        self.assertIn("JSONB", sql)
        self.assertNotIn("PASSWORD", sql.upper().replace("JSONB", ""))

    def test_env_example_has_placeholder_only(self):
        example = Path(__file__).resolve().parents[1].joinpath(".env.example").read_text(encoding="utf-8")
        self.assertIn("DATABASE_URL", example)
        self.assertIn("CHANGE_ME", example)


if __name__ == "__main__":
    unittest.main()
