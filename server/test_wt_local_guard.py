# -*- coding: utf-8 -*-
"""Проверки WT_LOCAL_TEST без подключения к PostgreSQL."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import wt_api
from wt_local_guard import (
    LocalTestGuardError,
    assert_safe_local_bind,
    assert_safe_local_database_url,
    enforce_local_test_guard,
    local_test_mode_enabled,
)

GOOD_URL = "postgresql://worktime_tester:secret@127.0.0.1:5433/worktime_test"


class WtLocalGuardUnitTests(unittest.TestCase):
    def test_mode_flag(self):
        self.assertFalse(local_test_mode_enabled({"WT_LOCAL_TEST": "0"}))
        self.assertTrue(local_test_mode_enabled({"WT_LOCAL_TEST": "1"}))
        self.assertTrue(local_test_mode_enabled({"WT_LOCAL_TEST": "true"}))

    def test_accepts_good_url(self):
        meta = assert_safe_local_database_url(GOOD_URL)
        self.assertEqual(meta["host"], "127.0.0.1")
        self.assertEqual(meta["port"], 5433)
        self.assertEqual(meta["database"], "worktime_test")
        self.assertEqual(meta["user"], "worktime_tester")

    def test_rejects_wrong_port(self):
        with self.assertRaises(LocalTestGuardError):
            assert_safe_local_database_url(
                "postgresql://worktime_tester:x@127.0.0.1:5432/worktime_test"
            )

    def test_rejects_wrong_db(self):
        with self.assertRaises(LocalTestGuardError):
            assert_safe_local_database_url(
                "postgresql://worktime_tester:x@127.0.0.1:5433/worktime"
            )

    def test_rejects_wrong_user(self):
        with self.assertRaises(LocalTestGuardError):
            assert_safe_local_database_url(
                "postgresql://postgres:x@127.0.0.1:5433/worktime_test"
            )

    def test_rejects_remote_host(self):
        with self.assertRaises(LocalTestGuardError):
            assert_safe_local_database_url(
                "postgresql://worktime_tester:x@db.example.com:5433/worktime_test"
            )

    def test_rejects_timeweb_marker(self):
        with self.assertRaises(LocalTestGuardError):
            assert_safe_local_database_url(
                "postgresql://worktime_tester:x@127.0.0.1:5433/worktime_test?app=timeweb"
            )

    def test_rejects_bind_all_interfaces(self):
        with self.assertRaises(LocalTestGuardError):
            assert_safe_local_bind("0.0.0.0")

    def test_accepts_bind_localhost(self):
        self.assertEqual(assert_safe_local_bind("localhost"), "localhost")

    def test_enforce_rejects_pg_bypass(self):
        env = {
            "WT_LOCAL_TEST": "1",
            "DATABASE_URL": GOOD_URL,
            "WT_BIND": "127.0.0.1",
            "PGHOST": "127.0.0.1",
        }
        with self.assertRaises(LocalTestGuardError) as ctx:
            enforce_local_test_guard(env)
        self.assertIn("PGHOST", str(ctx.exception))

    def test_enforce_ok(self):
        env = {
            "WT_LOCAL_TEST": "1",
            "DATABASE_URL": GOOD_URL,
            "WT_BIND": "127.0.0.1",
        }
        meta = enforce_local_test_guard(env)
        self.assertEqual(meta["database"], "worktime_test")
        self.assertEqual(meta["bind"], "127.0.0.1")

    def test_enforce_noop_without_flag(self):
        self.assertEqual(
            enforce_local_test_guard({"DATABASE_URL": "postgresql://x:y@evil:1/db"}),
            {},
        )

    def test_dsn_local_mode_ignores_pg_and_requires_url(self):
        with patch.dict(
            os.environ,
            {
                "WT_LOCAL_TEST": "1",
                "DATABASE_URL": GOOD_URL,
                "WT_BIND": "127.0.0.1",
            },
            clear=False,
        ):
            # Убрать возможные PG* из окружения агента
            for key in ("PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD"):
                os.environ.pop(key, None)
            self.assertEqual(wt_api.dsn(), GOOD_URL)

    def test_dsn_local_mode_blocks_bad_url_before_connect(self):
        with patch.dict(
            os.environ,
            {
                "WT_LOCAL_TEST": "1",
                "DATABASE_URL": "postgresql://worktime_tester:x@127.0.0.1:5432/worktime_test",
                "WT_BIND": "127.0.0.1",
            },
            clear=False,
        ):
            for key in ("PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD"):
                os.environ.pop(key, None)
            with self.assertRaises(Exception):
                wt_api.dsn()

    def test_main_aborts_on_bad_local_config(self):
        with patch.dict(
            os.environ,
            {
                "WT_LOCAL_TEST": "1",
                "DATABASE_URL": "postgresql://worktime_tester:x@8.8.8.8:5433/worktime_test",
                "WT_BIND": "127.0.0.1",
            },
            clear=False,
        ):
            for key in ("PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD"):
                os.environ.pop(key, None)
            with self.assertRaises(SystemExit) as ctx:
                wt_api.main([])
            self.assertEqual(ctx.exception.code, 2)

    def test_normal_mode_unchanged_without_local_flag(self):
        with patch.dict(os.environ, {"WT_LOCAL_TEST": "0"}, clear=False):
            os.environ.pop("WT_LOCAL_TEST", None)
            # Не должно бросать LocalTestGuardError при «плохом» URL — флаг выкл.
            with patch.dict(
                os.environ,
                {"DATABASE_URL": "postgresql://u:p@remote.example:5432/prod"},
                clear=False,
            ):
                os.environ.pop("WT_LOCAL_TEST", None)
                self.assertTrue(wt_api.dsn().startswith("postgresql://"))


if __name__ == "__main__":
    unittest.main()
