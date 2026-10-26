# -*- coding: utf-8 -*-
"""
Интеграционные тесты ops на настоящей PostgreSQL (только локальная worktime_test).

Запуск:
  $env:WT_TEST_DATABASE_URL = "postgresql://worktime_test:ПАРОЛЬ@127.0.0.1:5432/worktime_test"
  $env:WT_PIN_PEPPER = "unit-pepper"
  powershell -NoProfile -ExecutionPolicy Bypass -File server/run_wt_ops_pg_tests.ps1

Защита: перед любым SQL URL проверяется (127.0.0.1/localhost + БД worktime_test).
Без WT_TEST_DATABASE_URL тесты пропускаются. Небезопасный URL — жёсткая остановка.
"""

from __future__ import annotations

import json
import os
import threading
import unittest

from wt_ops_pg_safety import UnsafeTestDatabaseError, assert_safe_test_database_url

_RAW_URL = (os.environ.get("WT_TEST_DATABASE_URL") or "").strip()
_TEST_URL = ""
_URL_ERROR: str | None = None
if _RAW_URL:
    try:
        _TEST_URL = assert_safe_test_database_url(_RAW_URL)
    except UnsafeTestDatabaseError as exc:
        _URL_ERROR = str(exc)


class WtOpsPgUrlSafetyTests(unittest.TestCase):
    """Всегда выполняются: проверка правил безопасного DSN без подключения к БД."""

    def test_rejects_empty(self):
        with self.assertRaises(UnsafeTestDatabaseError):
            assert_safe_test_database_url("")

    def test_rejects_remote_host(self):
        with self.assertRaises(UnsafeTestDatabaseError):
            assert_safe_test_database_url(
                "postgresql://u:p@db.timeweb.cloud:5432/worktime_test"
            )

    def test_rejects_wrong_database_name(self):
        with self.assertRaises(UnsafeTestDatabaseError):
            assert_safe_test_database_url("postgresql://u:p@127.0.0.1:5432/worktime")

    def test_rejects_timeweb_marker(self):
        with self.assertRaises(UnsafeTestDatabaseError):
            assert_safe_test_database_url(
                "postgresql://u:p@127.0.0.1:5432/worktime_test?application_name=timeweb"
            )

    def test_accepts_local_worktime_test(self):
        url = "postgresql://worktime_test:secret@127.0.0.1:5432/worktime_test"
        self.assertEqual(assert_safe_test_database_url(url), url)


@unittest.skipIf(_URL_ERROR is not None, f"Unsafe WT_TEST_DATABASE_URL: {_URL_ERROR}")
@unittest.skipUnless(_TEST_URL, "WT_TEST_DATABASE_URL not set — skip real PostgreSQL tests")
class WtOpsPostgresIntegrationTests(unittest.TestCase):
    """Параллельные op/PUT против настоящей локальной PostgreSQL."""

    @classmethod
    def setUpClass(cls):
        if _URL_ERROR:
            raise unittest.SkipTest(_URL_ERROR)
        if not _TEST_URL:
            raise unittest.SkipTest("no test url")
        # Повторная проверка непосредственно перед SQL.
        assert_safe_test_database_url(_TEST_URL)
        os.environ["DATABASE_URL"] = _TEST_URL
        os.environ.setdefault("WT_PIN_PEPPER", "unit-pepper")
        os.environ.setdefault("WT_SCRYPT_N", "16")
        os.environ["WT_OPS_ENABLED"] = "1"
        os.environ.pop("WT_API_TOKEN", None)
        import wt_api
        from test_wt_api import SAMPLE_APP, call, cookie_from

        cls.wt_api = wt_api
        cls.call = staticmethod(call)
        cls.cookie_from = staticmethod(cookie_from)
        payload = json.loads(json.dumps(SAMPLE_APP))
        payload = wt_api.hash_pins_in_payload(payload, {})
        conn = cls._connect()
        try:
            with conn.cursor() as cur:
                # Схема как server/schema.sql (id = 1), не TEXT.
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS app_state (
                        id SMALLINT PRIMARY KEY CHECK (id = 1),
                        payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                        version INTEGER NOT NULL DEFAULT 0,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_by TEXT NOT NULL DEFAULT ''
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS app_sessions (
                        token_hash TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL,
                        expires_at TIMESTAMPTZ NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                cur.execute("DELETE FROM app_sessions")
                cur.execute("DELETE FROM app_state")
                cur.execute(
                    """
                    INSERT INTO app_state (id, payload, version, updated_by)
                    VALUES (%s, %s::jsonb, 1, 'test')
                    """,
                    (wt_api.STATE_ID, json.dumps(payload, ensure_ascii=False)),
                )
            conn.commit()
        finally:
            conn.close()

    @classmethod
    def _connect(cls):
        assert_safe_test_database_url(_TEST_URL)
        try:
            import psycopg

            return psycopg.connect(_TEST_URL)
        except ImportError:
            import psycopg2

            return psycopg2.connect(_TEST_URL)

    def _login(self, user_id="user_admin", pin="5555"):
        st, data, _, cookies = self.call(
            "POST", "/api/login", {"user_id": user_id, "pin": pin}
        )
        self.assertTrue(st.startswith("200"), st)
        return self.cookie_from(cookies), data

    def _payload(self):
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT payload FROM app_state WHERE id = %s",
                    (self.wt_api.STATE_ID,),
                )
                row = cur.fetchone()
            return row[0] if isinstance(row[0], dict) else json.loads(row[0])
        finally:
            conn.close()

    def test_01_parallel_independent_groups(self):
        admin_c, _ = self._login("user_admin", "5555")
        staff_c, _ = self._login("usr_001", "1111")
        results = {}

        def admin_job():
            st, d, *_ = self.call(
                "POST",
                "/api/state/op",
                {
                    "idempotency_key": "pg-admin-g",
                    "op": {
                        "path": "shopMetrics.facts.general_pg",
                        "expected_v": 0,
                        "value": "GENERAL",
                    },
                },
                cookie=admin_c,
            )
            results["admin"] = (st, d)

        def staff_job():
            st, d, *_ = self.call(
                "POST",
                "/api/state/op",
                {
                    "idempotency_key": "pg-staff-g",
                    "op": {
                        "path": "shopMetrics.facts.shop_pg",
                        "expected_v": 0,
                        "value": "SHOP",
                    },
                },
                cookie=staff_c,
            )
            results["staff"] = (st, d)

        t1 = threading.Thread(target=admin_job)
        t2 = threading.Thread(target=staff_job)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        self.assertTrue(results["admin"][0].startswith("200"), results)
        self.assertTrue(results["staff"][0].startswith("200"), results)
        facts = self._payload()["shopMetrics"]["facts"]
        self.assertEqual(facts.get("general_pg"), "GENERAL")
        self.assertEqual(facts.get("shop_pg"), "SHOP")

    def test_02_parallel_same_group_independent_cells(self):
        cookie, _ = self._login("usr_001", "1111")
        results = []

        def job(path, val, key):
            st, d, *_ = self.call(
                "POST",
                "/api/state/op",
                {"idempotency_key": key, "op": {"path": path, "expected_v": 0, "value": val}},
                cookie=cookie,
            )
            results.append((st, path, val))

        threads = [
            threading.Thread(target=job, args=("shopMetrics.facts.pg_c1", 11, "pg-c1")),
            threading.Thread(target=job, args=("shopMetrics.facts.pg_c2", 22, "pg-c2")),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertTrue(all(r[0].startswith("200") for r in results), results)
        facts = self._payload()["shopMetrics"]["facts"]
        self.assertEqual(facts.get("pg_c1"), 11)
        self.assertEqual(facts.get("pg_c2"), 22)

    def test_03_parallel_put_and_op(self):
        cookie, _ = self._login()
        results = {}

        def op_job():
            st, d, *_ = self.call(
                "POST",
                "/api/state/op",
                {"op": {"path": "shopMetrics.facts.pg_op_side", "expected_v": 0, "value": "OP"}},
                cookie=cookie,
            )
            results["op"] = st

        def put_job():
            for _ in range(8):
                # Актуальная version из БД
                conn = self._connect()
                try:
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT version FROM app_state WHERE id = %s",
                            (self.wt_api.STATE_ID,),
                        )
                        ver = int(cur.fetchone()[0])
                finally:
                    conn.close()
                st, *_ = self.call(
                    "PUT",
                    "/api/state",
                    {
                        "version": ver,
                        "payload": {"shopMetrics": {"facts": {"ok": 1, "pg_put_side": "PUT"}}},
                    },
                    cookie=cookie,
                )
                if st.startswith("200"):
                    results["put"] = st
                    return
                if st.startswith("409"):
                    continue
                results["put"] = st
                return
            results["put"] = "fail"

        t1 = threading.Thread(target=op_job)
        t2 = threading.Thread(target=put_job)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        self.assertTrue(str(results.get("op", "")).startswith("200"), results)
        self.assertTrue(str(results.get("put", "")).startswith("200"), results)
        facts = self._payload()["shopMetrics"]["facts"]
        self.assertEqual(facts.get("pg_op_side"), "OP")
        self.assertEqual(facts.get("pg_put_side"), "PUT")

    def test_04_same_record_conflict(self):
        cookie, _ = self._login()
        results = []

        def job(val, key):
            st, d, *_ = self.call(
                "POST",
                "/api/state/op",
                {
                    "idempotency_key": key,
                    "op": {"path": "shopMetrics.facts.pg_one", "expected_v": 0, "value": val},
                },
                cookie=cookie,
            )
            results.append((st, d, val))

        t1 = threading.Thread(target=job, args=("A", "pg-one-a"))
        t2 = threading.Thread(target=job, args=("B", "pg-one-b"))
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        oks = [r for r in results if r[0].startswith("200")]
        conflicts = [r for r in results if r[0].startswith("409")]
        self.assertEqual(len(oks), 1, results)
        self.assertEqual(len(conflicts), 1, results)
        self.assertEqual(
            self._payload()["shopMetrics"]["facts"]["pg_one"],
            oks[0][2],
        )

    def test_05_confirm_replace_after_conflict(self):
        cookie, _ = self._login()
        self.call(
            "POST",
            "/api/state/op",
            {"op": {"path": "shopMetrics.facts.pg_cell", "expected_v": 0, "value": "A"}},
            cookie=cookie,
        )
        st, data, *_ = self.call(
            "POST",
            "/api/state/op",
            {"op": {"path": "shopMetrics.facts.pg_cell", "expected_v": 1, "value": "B"}},
            cookie=cookie,
        )
        self.assertTrue(st.startswith("200"), st)
        self.assertEqual(data.get("value"), "B")
        self.assertEqual(self._payload()["shopMetrics"]["facts"]["pg_cell"], "B")

    def test_06_third_writer_before_confirm(self):
        cookie, _ = self._login()
        self.call(
            "POST",
            "/api/state/op",
            {"op": {"path": "shopMetrics.facts.pg_race", "expected_v": 0, "value": "A"}},
            cookie=cookie,
        )
        self.call(
            "POST",
            "/api/state/op",
            {"op": {"path": "shopMetrics.facts.pg_race", "expected_v": 1, "value": "C"}},
            cookie=cookie,
        )
        st, data, *_ = self.call(
            "POST",
            "/api/state/op",
            {"op": {"path": "shopMetrics.facts.pg_race", "expected_v": 1, "value": "B"}},
            cookie=cookie,
        )
        self.assertTrue(st.startswith("409"), st)
        self.assertEqual(data.get("server_value"), "C")
        self.assertEqual(self._payload()["shopMetrics"]["facts"]["pg_race"], "C")

    def test_07_idempotent_retry(self):
        cookie, _ = self._login()
        body = {
            "idempotency_key": "pg-retry-key",
            "op": {"path": "shopMetrics.facts.pg_once", "expected_v": 0, "value": 42},
        }
        st1, d1, *_ = self.call("POST", "/api/state/op", body, cookie=cookie)
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT version FROM app_state WHERE id = %s",
                    (self.wt_api.STATE_ID,),
                )
                ver1 = int(cur.fetchone()[0])
        finally:
            conn.close()
        st2, d2, *_ = self.call("POST", "/api/state/op", body, cookie=cookie)
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT version FROM app_state WHERE id = %s",
                    (self.wt_api.STATE_ID,),
                )
                ver2 = int(cur.fetchone()[0])
        finally:
            conn.close()
        self.assertTrue(st1.startswith("200"))
        self.assertTrue(st2.startswith("200"))
        self.assertEqual(d1.get("value"), 42)
        self.assertEqual(d2.get("value"), 42)
        self.assertEqual(ver1, ver2)
        self.assertEqual(self._payload()["shopMetrics"]["facts"]["pg_once"], 42)

    def test_08_wide_admin_put_keeps_op(self):
        cookie, _ = self._login()
        self.call(
            "POST",
            "/api/state/op",
            {"op": {"path": "shopMetrics.facts.pg_guarded", "expected_v": 0, "value": "KEEP"}},
            cookie=cookie,
        )
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT version FROM app_state WHERE id = %s",
                    (self.wt_api.STATE_ID,),
                )
                ver = int(cur.fetchone()[0])
        finally:
            conn.close()
        st, *_ = self.call(
            "PUT",
            "/api/state",
            {
                "version": ver,
                "payload": {
                    "shopMetrics": {"facts": {"ok": 1, "pg_admin_extra": 5}},
                    "_wt_records": {},
                },
            },
            cookie=cookie,
        )
        self.assertTrue(st.startswith("200"), st)
        facts = self._payload()["shopMetrics"]["facts"]
        self.assertEqual(facts.get("pg_guarded"), "KEEP")
        self.assertEqual(facts.get("pg_admin_extra"), 5)

    def test_09_ops_disabled_keeps_values(self):
        cookie, _ = self._login()
        self.call(
            "POST",
            "/api/state/op",
            {"op": {"path": "shopMetrics.facts.pg_kept", "expected_v": 0, "value": "keep_me"}},
            cookie=cookie,
        )
        from unittest.mock import patch

        with patch.dict(os.environ, {"WT_OPS_ENABLED": "0"}, clear=False):
            st_op, *_ = self.call(
                "POST",
                "/api/state/op",
                {"op": {"path": "shopMetrics.facts.pg_kept", "expected_v": 1, "value": "nope"}},
                cookie=cookie,
            )
            self.assertTrue(st_op.startswith("503"), st_op)
            conn = self._connect()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT version FROM app_state WHERE id = %s",
                        (self.wt_api.STATE_ID,),
                    )
                    ver = int(cur.fetchone()[0])
            finally:
                conn.close()
            st_put, *_ = self.call(
                "PUT",
                "/api/state",
                {
                    "version": ver,
                    "payload": {"shopMetrics": {"facts": {"ok": 1}}, "_wt_records": {}},
                },
                cookie=cookie,
            )
            self.assertTrue(st_put.startswith("200"), st_put)
        self.assertEqual(self._payload()["shopMetrics"]["facts"].get("pg_kept"), "keep_me")

    def test_10_record_versions_after_parallel_series(self):
        cookie, _ = self._login()
        # Серия независимых op → у каждого пути свой record_v >= 1, без потери соседей.
        paths = [f"shopMetrics.facts.pg_series_{i}" for i in range(6)]
        results = {}

        def job(path, i):
            st, d, *_ = self.call(
                "POST",
                "/api/state/op",
                {
                    "idempotency_key": f"pg-ser-{i}",
                    "op": {"path": path, "expected_v": 0, "value": i * 10},
                },
                cookie=cookie,
            )
            results[path] = (st, d)

        threads = [threading.Thread(target=job, args=(p, i)) for i, p in enumerate(paths)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertTrue(all(results[p][0].startswith("200") for p in paths), results)
        payload = self._payload()
        facts = payload["shopMetrics"]["facts"]
        meta = payload.get("_wt_records") or {}
        for i, path in enumerate(paths):
            self.assertEqual(facts.get(path.split(".")[-1]), i * 10)
            self.assertGreaterEqual(int((meta.get(path) or {}).get("v") or 0), 1)


if __name__ == "__main__":
    # Если URL задан, но небезопасен — падаем до SQL (не silent skip всего файла).
    if _URL_ERROR:
        raise SystemExit(f"ABORT: {_URL_ERROR}")
    unittest.main()
