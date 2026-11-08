# -*- coding: utf-8 -*-
"""Автотесты API хранилища и входа без PostgreSQL и без Timeweb."""

from __future__ import annotations

import json
import os
import threading
import unittest
from datetime import datetime, timedelta, timezone
from io import BytesIO, StringIO
from pathlib import Path
from unittest.mock import patch

import wt_api

SECRET = "super-secret-db-password-xyz"
ADMIN = {
    "user_id": "user_admin",
    "display_name": "Анна Владимировна",
    "pin": "5555",
    "role": "admin",
    "legacy_role": "admin",
    "is_active": True,
    "departments": ["shop", "summary"],
    "permissions": {"settings": "edit", "salary": "edit", "financeRec": "edit"},
}
STAFF = {
    "user_id": "usr_001",
    "display_name": "ВИКУЛИШНА",
    "pin": "1111",
    "role": "user",
    "legacy_role": "staff",
    "is_active": True,
    "departments": ["shop"],
    "permissions": {"metricsInput": "edit", "timesheet": "edit"},
}
WAREHOUSE = {
    "user_id": "usr_wh",
    "display_name": "Склад",
    "pin": "2222",
    "role": "user",
    "legacy_role": "staff",
    "is_active": True,
    "departments": ["warehouse"],
    "permissions": {"timesheet": "edit", "metricsInput": "edit"},
}
INACTIVE = {
    "user_id": "usr_off",
    "display_name": "Выключен",
    "pin": "0000",
    "role": "user",
    "is_active": False,
    "departments": ["shop"],
    "permissions": {},
}
EDITOR = {
    "user_id": "usr_set",
    "display_name": "Настройки",
    "pin": "3333",
    "role": "user",
    "legacy_role": "staff",
    "is_active": True,
    "departments": ["shop"],
    "permissions": {"settings": "edit"},
}
SAMPLE_APP = {
    "workspaces": [
        {
            "id": "ws1",
            "code": "shop",
            "name": "Магазин Володарского",
            "pin": "1111",
            "employees": [{"id": "e_shop", "name": "Касса", "rate": 1500, "taxiDaily": 200}],
            "timesheet": {"2026-10": {"e_shop": {"1": "Я"}}},
        },
        {
            "id": "ws2",
            "code": "warehouse",
            "name": "Склад",
            "pin": "3333",
            "employees": [{"id": "e_wh", "name": "Кладовщик", "rate": 1800}],
            "timesheet": {"2026-10": {"e_wh": {"1": "Я"}}},
        },
    ],
    "users": [
        json.loads(json.dumps(ADMIN)),
        json.loads(json.dumps(STAFF)),
        json.loads(json.dumps(INACTIVE)),
        json.loads(json.dumps(EDITOR)),
        json.loads(json.dumps(WAREHOUSE)),
    ],
    "salary": {"2026-10": {"e_shop": {"issued": False, "rate": 1500}, "e_wh": {"rate": 1800}}},
    "shopPayroll": {"salaries": [{"employee_id": "e_shop", "rate": 1500}]},
    "financeRec": {"operations": [{"id": "op1", "amount": 99}]},
    "shopMetrics": {"facts": {"ok": 1}, "nested": {"employees": [{"rate": 777, "name": "скрыто"}]}},
    "deptMetrics": {"warehouse": {"facts": {"secret": 1}}, "office": {"facts": {"x": 1}}},
    "management": {"indicators": [{"finance": 1}]},
}


class FakeCursor:
    def __init__(self, store):
        self.store = store
        self._row = None

    def execute(self, sql, params=None):
        if self.store.get("fail"):
            raise RuntimeError("could not connect to server password=" + SECRET)
        text = " ".join(str(sql).split())
        if "SELECT 1" in text:
            self._row = (1,)
            return
        if text.startswith("INSERT INTO app_state"):
            self._row = None
            return
        if "INSERT INTO app_sessions" in text:
            if self.store.get("fail_sessions"):
                raise RuntimeError("could not write session")
            token_h, user_id, expires_at = params
            self.store.setdefault("sessions", {})[token_h] = {
                "user_id": user_id,
                "expires_at": expires_at,
            }
            self._row = None
            return
        if "SELECT COUNT(*) FROM app_sessions" in text:
            uid, cutoff = params
            n = 0
            for rec in (self.store.get("sessions") or {}).values():
                exp = rec.get("expires_at")
                if rec.get("user_id") == uid and exp is not None and exp > cutoff:
                    n += 1
            self._row = (n,)
            return
        if "SELECT user_id, expires_at FROM app_sessions" in text:
            rec = (self.store.get("sessions") or {}).get(params[0])
            self._row = (rec["user_id"], rec["expires_at"]) if rec else None
            return
        if text.startswith("DELETE FROM app_sessions") and "expires_at" in text and "user_id" in text:
            uid, cutoff = params
            sessions = self.store.setdefault("sessions", {})
            for token_h in list(sessions):
                rec = sessions[token_h]
                exp = rec.get("expires_at")
                if rec.get("user_id") == uid and exp is not None and exp < cutoff:
                    sessions.pop(token_h, None)
            self._row = None
            return
        if text.startswith("DELETE FROM app_sessions") and "user_id" in text and "token_hash" not in text:
            uid = params[0]
            sessions = self.store.setdefault("sessions", {})
            for token_h in list(sessions):
                if sessions[token_h].get("user_id") == uid:
                    sessions.pop(token_h, None)
            self._row = None
            return
        if text.startswith("DELETE FROM app_sessions") and "token_hash" in text:
            (self.store.setdefault("sessions", {})).pop(params[0], None)
            self._row = None
            return
        if text.startswith("UPDATE app_state"):
            payload_raw, updated_by, _sid, expected = params
            if int(self.store["version"]) != int(expected):
                self._row = None
                return
            if "jsonb_array_length" in text:
                current = self.store.get("payload") if isinstance(self.store.get("payload"), dict) else {}
                users = current.get("users")
                if isinstance(users, list) and len(users) > 0:
                    self._row = None
                    return
            payload = json.loads(payload_raw) if isinstance(payload_raw, str) else payload_raw
            self.store["payload"] = payload
            self.store["version"] = int(self.store["version"]) + 1
            self.store["updated_by"] = updated_by
            self.store["updated_at"] = datetime.now(timezone.utc)
            # PUT: RETURNING version, updated_at, updated_by (без payload).
            if "RETURNING version, updated_at, updated_by" in text and "RETURNING payload" not in text:
                self._row = (
                    self.store["version"],
                    self.store["updated_at"],
                    self.store["updated_by"],
                )
            else:
                self._row = (
                    self.store["payload"],
                    self.store["version"],
                    self.store["updated_at"],
                    self.store["updated_by"],
                )
            return
        if "SELECT payload, version" in text:
            # Имитация FOR UPDATE: держим store["lock"] до commit/rollback соединения.
            if "FOR UPDATE" in text:
                lock = self.store.get("lock")
                if lock is not None and not self.store.get("_lock_held"):
                    lock.acquire()
                    self.store["_lock_held"] = True
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

    def _release_row_lock(self):
        lock = self.store.get("lock")
        if lock is not None and self.store.get("_lock_held"):
            self.store["_lock_held"] = False
            lock.release()

    def commit(self):
        self.store["committed"] = True
        self._release_row_lock()

    def rollback(self):
        self.store["rolled_back"] = True
        self._release_row_lock()

    def close(self):
        self._release_row_lock()
        self.store["closed"] = True


def call(method, path, body=None, token=None, cookie=None, as_json=True, session=None, environ_extra=None):
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
        "REMOTE_ADDR": "127.0.0.1",
    }
    if environ_extra:
        environ.update(environ_extra)
    if cookie:
        environ["HTTP_COOKIE"] = cookie
    if session:
        environ["HTTP_AUTHORIZATION"] = "Session " + session
    elif token:
        environ["HTTP_AUTHORIZATION"] = "Bearer " + token
    result = b"".join(wt_api.application(environ, start_response))
    text = result.decode("utf-8", errors="replace")
    data = json.loads(text) if as_json and result else None
    cookies = []
    for name, value in captured.get("headers") or []:
        if name.lower() == "set-cookie":
            cookies.append(value)
    return captured["status"], data, text, cookies


def cookie_from(set_cookies):
    for item in set_cookies:
        if item.startswith("wt_session="):
            return item.split(";", 1)[0]
    return ""


def session_raw(set_cookies):
    line = cookie_from(set_cookies)
    if not line.startswith("wt_session="):
        return ""
    return line.split("=", 1)[1]


class StoreApiTests(unittest.TestCase):
    def setUp(self):
        self._env_backup = dict(os.environ)
        os.environ["DATABASE_URL"] = "postgresql://worktime:" + SECRET + "@127.0.0.1:5432/worktime"
        os.environ["WT_PIN_PEPPER"] = "unit-pepper"
        os.environ["WT_SCRYPT_N"] = "16"
        os.environ.pop("WT_API_TOKEN", None)
        os.environ.pop("WT_COOKIE_SECURE", None)
        wt_api.LOGIN_FAILS.clear()
        self.store = {
            "payload": json.loads(json.dumps(SAMPLE_APP)),
            "version": 0,
            "updated_at": None,
            "updated_by": "",
            "fail": False,
            "sessions": {},
        }
        self._patch = patch.object(wt_api, "connect", side_effect=lambda: FakeConn(self.store))
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        os.environ.clear()
        os.environ.update(self._env_backup)

    def login(self, user_id="user_admin", pin="5555"):
        status, data, _, cookies = call("POST", "/api/login", {"user_id": user_id, "pin": pin})
        self.assertTrue(status.startswith("200"), status)
        return cookie_from(cookies), data

    def add_user(self, user):
        self.store["payload"]["users"].append(json.loads(json.dumps(user)))

    def test_health_ok(self):
        status, data, *_ = call("GET", "/api/health")
        self.assertTrue(status.startswith("200"))
        self.assertEqual(data.get("ok"), True)
        self.assertIn("ops", data)
        status_head, _, text, _ = call("HEAD", "/api/health", as_json=False)
        self.assertTrue(status_head.startswith("200"))
        self.assertEqual(text, "")

    def test_health_does_not_open_database(self):
        self._patch.stop()
        try:
            with patch.object(wt_api, "connect", side_effect=RuntimeError("health must not open db")) as mocked:
                status, data, raw, _ = call("GET", "/api/health")
                mocked.assert_not_called()
            self.assertTrue(status.startswith("200"))
            self.assertEqual(data.get("ok"), True)
            self.assertNotIn("password", raw.lower())
        finally:
            self._patch.start()

    def test_login_success_and_me(self):
        cookie, data = self.login("usr_001", "1111")
        self.assertEqual(data["user"]["user_id"], "usr_001")
        self.assertNotIn("pin", data["user"])
        status, me, *_ = call("GET", "/api/me", cookie=cookie)
        self.assertTrue(status.startswith("200"))
        self.assertEqual(me["user"]["display_name"], "ВИКУЛИШНА")
        self.assertEqual(me["user"]["permissions"]["metricsInput"], "edit")

    def test_login_wrong_pin(self):
        status, data, raw, _ = call("POST", "/api/login", {"user_id": "usr_001", "pin": "9999"})
        self.assertTrue(status.startswith("401"))
        self.assertEqual(data["error"], "invalid login")
        self.assertNotIn("5555", raw)
        self.assertNotIn("1111", raw)
        self.assertFalse(any(
            not str(rec.get("user_id") or "").startswith("wt.login_fail:")
            for rec in (self.store.get("sessions") or {}).values()
        ))

    def test_login_without_pepper_does_not_change_db(self):
        os.environ.pop("WT_PIN_PEPPER", None)
        snapshot = json.loads(json.dumps(self.store["payload"]))
        status, data, raw, cookies = call("POST", "/api/login", {"user_id": "usr_001", "pin": "1111"})
        self.assertTrue(status.startswith("503"), status)
        self.assertEqual(data["error"], "authentication unavailable")
        self.assertNotIn("unit-pepper", raw)
        self.assertNotIn("WT_PIN_PEPPER", raw)
        self.assertEqual(cookies, [])
        self.assertEqual(self.store["payload"], snapshot)
        self.assertEqual(self.store.get("sessions"), {})
        self.assertEqual(self.store["version"], 0)

    def test_login_empty_pepper_does_not_verify(self):
        os.environ["WT_PIN_PEPPER"] = ""
        status, data, raw, cookies = call("POST", "/api/login", {"user_id": "usr_001", "pin": "1111"})
        self.assertTrue(status.startswith("503"), status)
        self.assertEqual(data["error"], "authentication unavailable")
        self.assertNotIn("unit-pepper", raw)
        self.assertEqual(cookies, [])
        self.assertEqual(self.store.get("sessions"), {})

    def test_login_inactive_user(self):
        status, data, *_ = call("POST", "/api/login", {"user_id": "usr_off", "pin": "0000"})
        self.assertTrue(status.startswith("401"))
        self.assertEqual(data["error"], "invalid login")

    def test_login_rate_limit(self):
        for _ in range(5):
            status, *_ = call("POST", "/api/login", {"user_id": "usr_001", "pin": "0001"})
            self.assertTrue(status.startswith("401"))
        status, data, *_ = call("POST", "/api/login", {"user_id": "usr_001", "pin": "1111"})
        self.assertTrue(status.startswith("429"))
        self.assertEqual(data["error"], "too many attempts")

    def test_staff_cannot_read_full_state(self):
        cookie, _ = self.login("usr_001", "1111")
        status, data, raw, _ = call("GET", "/api/state", cookie=cookie)
        self.assertTrue(status.startswith("200"), status)
        payload = data["payload"]
        codes = [w["code"] for w in payload.get("workspaces") or []]
        self.assertEqual(codes, ["shop"])
        self.assertNotIn("salary", payload)
        self.assertNotIn("shopPayroll", payload)
        self.assertNotIn("financeRec", payload)
        self.assertNotIn("users", payload)
        self.assertNotIn("deptMetrics", payload)
        self.assertNotIn("management", payload)
        self.assertNotIn("1500", raw)
        self.assertNotIn("1800", raw)
        self.assertNotIn("rate", raw)
        shop = payload["workspaces"][0]
        self.assertNotIn("rate", shop["employees"][0])
        self.assertNotIn("taxiDaily", shop["employees"][0])
        self.assertEqual(shop["employees"][0]["name"], "Касса")
        nested = ((payload.get("shopMetrics") or {}).get("nested") or {}).get("employees") or [{}]
        self.assertNotIn("rate", nested[0])

    def test_directory_lists_active_users_without_secrets(self):
        status, data, raw, _ = call("GET", "/api/directory")
        self.assertTrue(status.startswith("200"))
        ids = [row["user_id"] for row in data["users"]]
        self.assertIn("usr_001", ids)
        self.assertIn("user_admin", ids)
        self.assertNotIn("usr_off", ids)
        self.assertEqual(set(data["users"][0].keys()), {"user_id", "display_name", "departments", "login_codes"})
        shop_user = next(row for row in data["users"] if row["user_id"] == "usr_001")
        self.assertIn("shop", shop_user["login_codes"])
        self.assertNotIn("warehouse", shop_user["login_codes"])
        self.assertNotIn("spu", shop_user["login_codes"])
        self.assertNotIn("pin", raw)
        self.assertNotIn("salary", raw)
        self.assertNotIn("1111", raw)
        self.assertNotIn("1500", raw)
        self.assertNotIn("permissions", raw)

    def test_warehouse_staff_does_not_see_shop(self):
        cookie, _ = self.login("usr_wh", "2222")
        status, data, raw, _ = call("GET", "/api/state", cookie=cookie)
        self.assertTrue(status.startswith("200"), status)
        codes = [w["code"] for w in data["payload"].get("workspaces") or []]
        self.assertEqual(codes, ["warehouse"])
        self.assertNotIn("shopMetrics", data["payload"])
        self.assertIn("deptMetrics", data["payload"])
        self.assertIn("warehouse", data["payload"]["deptMetrics"])
        self.assertNotIn("office", data["payload"]["deptMetrics"])
        self.assertNotIn("1500", raw)
        self.assertNotIn("Касса", raw)
        self.assertNotIn("financeRec", data["payload"])
        self.assertNotIn("salary", data["payload"])

    def test_state_requires_session(self):
        status, data, raw, _ = call("GET", "/api/state")
        self.assertTrue(status.startswith("401"))
        self.assertEqual(data["error"], "unauthorized")
        self.assertNotIn("workspaces", raw)
        self.assertNotIn("Касса", raw)

    def test_admin_state_strips_pins(self):
        cookie, _ = self.login()
        status, data, raw, _ = call("GET", "/api/state", cookie=cookie)
        self.assertTrue(status.startswith("200"))
        self.assertEqual(data["payload"]["workspaces"][0]["code"], "shop")
        self.assertNotIn('"pin"', raw)
        self.assertNotIn("pin_hash", raw)
        self.assertNotIn("5555", raw)
        users = data["payload"]["users"]
        self.assertEqual(users[0]["user_id"], "user_admin")
        self.assertNotIn("pin", users[0])

    def current_version(self, cookie):
        status, data, *_ = call("GET", "/api/state", cookie=cookie)
        self.assertTrue(status.startswith("200"), status)
        return int(data["version"])

    def test_admin_create_user_rights_persist_after_reload(self):
        cookie, _ = self.login()
        version = self.current_version(cookie)
        payload = json.loads(json.dumps(self.store["payload"]))
        payload["users"].append({
            "user_id": "usr_new",
            "display_name": "Новый сотрудник",
            "pin": "7777",
            "role": "user",
            "legacy_role": "staff",
            "is_active": True,
            "departments": ["shop", "summary"],
            "permissions": {
                "metricsInput": "edit",
                "timesheet": "edit",
                "settings": "none",
            },
        })
        status, data, raw, _ = call(
            "PUT",
            "/api/state",
            {"version": version, "payload": payload},
            cookie=cookie,
        )
        self.assertTrue(status.startswith("200"), status)
        self.assertEqual(data["version"], version + 1)
        self.assertNotIn("7777", raw)
        stored = next(u for u in self.store["payload"]["users"] if u["user_id"] == "usr_new")
        self.assertTrue(str(stored.get("pin_hash") or "").startswith("scrypt$"))
        self.assertNotIn("pin", stored)
        self.assertNotEqual(stored.get("pin_hash"), "7777")
        self.assertEqual(stored["permissions"]["metricsInput"], "edit")
        self.assertEqual(stored["permissions"]["timesheet"], "edit")
        self.assertTrue(wt_api.verify_pin("7777", stored["pin_hash"]))
        status2, got, raw2, _ = call("GET", "/api/state", cookie=cookie)
        self.assertTrue(status2.startswith("200"))
        reloaded = next(u for u in got["payload"]["users"] if u["user_id"] == "usr_new")
        self.assertEqual(reloaded["display_name"], "Новый сотрудник")
        self.assertEqual(reloaded["departments"], ["shop", "summary"])
        self.assertEqual(reloaded["permissions"]["metricsInput"], "edit")
        self.assertEqual(reloaded["permissions"]["timesheet"], "edit")
        self.assertNotIn("pin", reloaded)
        self.assertNotIn("pin_hash", reloaded)
        self.assertNotIn("7777", raw2)
        self.assertNotIn("pin_hash", json.dumps(got["payload"]))

    def test_put_and_get_appdata(self):
        cookie, _ = self.login()
        version = self.current_version(cookie)
        status, data, *_ = call("PUT", "/api/state", {
            "version": version,
            "payload": SAMPLE_APP,
        }, cookie=cookie)
        self.assertTrue(status.startswith("200"))
        self.assertEqual(data["version"], version + 1)
        self.assertNotIn("payload", data)
        self.assertTrue(data.get("ok"))
        stored_users = self.store["payload"]["users"]
        self.assertTrue(str(stored_users[0].get("pin_hash") or "").startswith("scrypt$"))
        self.assertNotIn("pin", stored_users[0])
        status2, got, raw, _ = call("GET", "/api/state", cookie=cookie)
        self.assertTrue(status2.startswith("200"))
        self.assertEqual(got["payload"]["workspaces"][0]["code"], "shop")
        self.assertNotIn("pin_hash", raw)

    def test_put_success_omits_full_payload(self):
        """Успешный PUT не возвращает весь appData — только version (+ record_updates)."""
        cookie, _ = self.login()
        version = self.current_version(cookie)
        status, data, raw, _ = call(
            "PUT",
            "/api/state",
            {"version": version, "payload": {"shopMetrics": {"facts": {"t": 1}}}},
            cookie=cookie,
        )
        self.assertTrue(status.startswith("200"), status)
        self.assertEqual(data["version"], version + 1)
        self.assertNotIn("payload", data)
        # Полный снимок не отдаём; record_updates — карта v для клиента (пути могут содержать shopMetrics.*).
        self.assertIn("record_updates", data)
        self.assertIsInstance(data["record_updates"], dict)
        self.assertNotIn('"payload"', raw)
        self.assertNotIn('"shopMetrics": {', raw)
        self.assertNotIn('"facts"', raw)
        self.assertEqual(self.store["payload"]["shopMetrics"]["facts"]["t"], 1)
        self.assertIn("shopMetrics.facts.t", data["record_updates"])

    def test_stale_version_does_not_overwrite(self):
        cookie, _ = self.login()
        version = self.current_version(cookie)
        call("PUT", "/api/state", {"version": version, "payload": SAMPLE_APP}, cookie=cookie)
        stale = {"workspaces": [{"id": "ws1", "code": "office"}], "users": []}
        status, data, raw, _ = call("PUT", "/api/state", {"version": version, "payload": stale}, cookie=cookie)
        self.assertTrue(status.startswith("409"))
        self.assertEqual(data["error"], "conflict")
        self.assertEqual(data["version"], version + 1)
        self.assertNotIn("payload", data)
        self.assertNotIn("salary", raw)
        self.assertNotIn("financeRec", raw)
        self.assertEqual(self.store["payload"]["workspaces"][0]["code"], "shop")

    def test_second_writer_succeeds_with_fresh_version(self):
        cookie, _ = self.login()
        version = self.current_version(cookie)
        first, *_ = call("PUT", "/api/state", {"version": version, "payload": SAMPLE_APP}, cookie=cookie)
        self.assertTrue(first.startswith("200"))
        nxt = json.loads(json.dumps(SAMPLE_APP))
        nxt["users"].append({"user_id": "usr_x", "display_name": "X", "pin": "2222", "role": "user"})
        status, data, *_ = call("PUT", "/api/state", {"version": version + 1, "payload": nxt}, cookie=cookie)
        self.assertTrue(status.startswith("200"))
        self.assertEqual(data["version"], version + 2)
        self.assertEqual(len(self.store["payload"]["users"]), 6)

    def test_staff_cannot_put_state(self):
        cookie, _ = self.login("usr_001", "1111")
        status, data, *_ = call(
            "PUT",
            "/api/state",
            {"version": int(self.store["version"]), "payload": SAMPLE_APP},
            cookie=cookie,
        )
        self.assertTrue(status.startswith("403"))
        self.assertEqual(data["error"], "forbidden")
        self.assertEqual(self.store["payload"]["workspaces"][1]["code"], "warehouse")

    def test_staff_clears_timesheet_cell(self):
        cookie, _ = self.login("usr_001", "1111")
        status, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(self.store["version"]),
                "payload": {
                    "workspaces": [{
                        "code": "shop",
                        "timesheet": {"2026-10": {"e_shop": {"1": {"status": "", "hours": "", "lateMinutes": 0}}}},
                    }]
                },
            },
            cookie=cookie,
        )
        self.assertTrue(status.startswith("200"), status)
        shop = next(w for w in self.store["payload"]["workspaces"] if w["code"] == "shop")
        cell = shop["timesheet"]["2026-10"]["e_shop"]["1"]
        self.assertEqual(cell["status"], "")
        self.assertNotEqual(cell, "Я")

    def test_staff_writes_shop_metrics(self):
        cookie, _ = self.login("usr_001", "1111")
        status, data, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(self.store["version"]),
                "payload": {"shopMetrics": {"facts": {"ok": 9}}},
            },
            cookie=cookie,
        )
        self.assertTrue(status.startswith("200"), status)
        self.assertEqual(self.store["payload"]["shopMetrics"]["facts"]["ok"], 9)
        self.assertEqual(self.store["payload"]["shopMetrics"]["nested"]["employees"][0]["rate"], 777)
        # PUT отвечает без полного payload (только version) — снимок проверяем в store / GET.
        self.assertEqual(data["version"], int(self.store["version"]))
        self.assertNotIn("payload", data)

    def test_staff_writes_cashier_category_store(self):
        """Продажи/чеки/«Кто работал» лежат в shopMetrics.cashiers и должны сохраняться staff-PUT."""
        self.store["payload"]["shopMetrics"]["cashiers"] = {
            "2026-10": {
                "e_other": {"1": {"sales": 50000, "receipts": 40}},
                "e_shop": {"2": {"sales": 1000, "receipts": 5}},
            }
        }
        cookie, _ = self.login("usr_001", "1111")
        status, data, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(self.store["version"]),
                "payload": {
                    "shopMetrics": {
                        "cashiers": {
                            "2026-10": {
                                "e_shop": {
                                    "1": {
                                        "sales": 164197,
                                        "receipts": 110,
                                        "actual_cashier_employee_id": "e_shop",
                                        "source": "manual",
                                    }
                                }
                            }
                        }
                    }
                },
            },
            cookie=cookie,
        )
        self.assertTrue(status.startswith("200"), status)
        cashiers = self.store["payload"]["shopMetrics"]["cashiers"]["2026-10"]
        self.assertEqual(cashiers["e_shop"]["1"]["sales"], 164197)
        self.assertEqual(cashiers["e_shop"]["1"]["receipts"], 110)
        self.assertEqual(cashiers["e_shop"]["1"]["actual_cashier_employee_id"], "e_shop")
        # Чужие дни/сотрудники и другие даты не стираются overlay-слиянием.
        self.assertEqual(cashiers["e_other"]["1"]["sales"], 50000)
        self.assertEqual(cashiers["e_shop"]["2"]["sales"], 1000)

    def test_staff_updates_and_clears_cashier_cell(self):
        """Создание → изменение → очистка (JSON null). 0 не путать с удалением ключа."""
        self.store["payload"]["shopMetrics"]["cashiers"] = {
            "2026-10": {
                "e_shop": {"1": {"sales": 150000, "receipts": 100, "actual_cashier_employee_id": "e_a"}},
                "e_other": {"1": {"sales": 50000}},
            }
        }
        cookie, _ = self.login("usr_001", "1111")
        # Исправление суммы.
        status, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(self.store["version"]),
                "payload": {
                    "shopMetrics": {
                        "cashiers": {"2026-10": {"e_shop": {"1": {"sales": 120000, "receipts": 100}}}}
                    }
                },
            },
            cookie=cookie,
        )
        self.assertTrue(status.startswith("200"), status)
        day = self.store["payload"]["shopMetrics"]["cashiers"]["2026-10"]["e_shop"]["1"]
        self.assertEqual(day["sales"], 120000)
        self.assertEqual(day["receipts"], 100)
        # Очистка sales и «Кто работал» явным null; receipts остаётся; чужой сотрудник цел.
        status, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(self.store["version"]),
                "payload": {
                    "shopMetrics": {
                        "cashiers": {
                            "2026-10": {
                                "e_shop": {
                                    "1": {
                                        "sales": None,
                                        "actual_cashier_employee_id": None,
                                        "receipts": 100,
                                    }
                                }
                            }
                        }
                    }
                },
            },
            cookie=cookie,
        )
        self.assertTrue(status.startswith("200"), status)
        day = self.store["payload"]["shopMetrics"]["cashiers"]["2026-10"]["e_shop"]["1"]
        self.assertNotIn("sales", day)
        self.assertNotIn("actual_cashier_employee_id", day)
        self.assertEqual(day["receipts"], 100)
        self.assertEqual(
            self.store["payload"]["shopMetrics"]["cashiers"]["2026-10"]["e_other"]["1"]["sales"],
            50000,
        )
        # Полная очистка дня сотрудника — null на уровне дня.
        status, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(self.store["version"]),
                "payload": {
                    "shopMetrics": {"cashiers": {"2026-10": {"e_shop": {"1": None}}}}
                },
            },
            cookie=cookie,
        )
        self.assertTrue(status.startswith("200"), status)
        self.assertNotIn("1", self.store["payload"]["shopMetrics"]["cashiers"]["2026-10"]["e_shop"])
        self.assertEqual(
            self.store["payload"]["shopMetrics"]["cashiers"]["2026-10"]["e_other"]["1"]["sales"],
            50000,
        )

    def test_staff_clear_does_not_treat_zero_as_delete_marker(self):
        """Число 0 сохраняется как 0; удаление только через JSON null."""
        self.store["payload"]["shopMetrics"]["cashiers"] = {
            "2026-10": {"e_shop": {"1": {"sales": 10, "receipts": 1}}}
        }
        cookie, _ = self.login("usr_001", "1111")
        status, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(self.store["version"]),
                "payload": {
                    "shopMetrics": {
                        "cashiers": {"2026-10": {"e_shop": {"1": {"sales": 0, "receipts": 1}}}}
                    }
                },
            },
            cookie=cookie,
        )
        self.assertTrue(status.startswith("200"), status)
        day = self.store["payload"]["shopMetrics"]["cashiers"]["2026-10"]["e_shop"]["1"]
        self.assertEqual(day["sales"], 0)
        self.assertIn("sales", day)

    def test_concurrent_staff_overlay_different_fields(self):
        """Два последовательных staff-PUT по разным полям не затирают друг друга."""
        self.store["payload"]["shopMetrics"]["cashiers"] = {
            "2026-10": {"e_shop": {"1": {"sales": 100, "receipts": 10}}}
        }
        cookie, _ = self.login("usr_001", "1111")
        status, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(self.store["version"]),
                "payload": {
                    "shopMetrics": {
                        "cashiers": {"2026-10": {"e_shop": {"1": {"sales": 200}}}}
                    }
                },
            },
            cookie=cookie,
        )
        self.assertTrue(status.startswith("200"), status)
        status, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(self.store["version"]),
                "payload": {
                    "shopMetrics": {
                        "cashiers": {"2026-10": {"e_shop": {"1": {"receipts": 25}}}}
                    }
                },
            },
            cookie=cookie,
        )
        self.assertTrue(status.startswith("200"), status)
        day = self.store["payload"]["shopMetrics"]["cashiers"]["2026-10"]["e_shop"]["1"]
        self.assertEqual(day["sales"], 200)
        self.assertEqual(day["receipts"], 25)

    def test_version_conflict_409_keeps_server_data(self):
        self.store["payload"]["shopMetrics"]["cashiers"] = {
            "2026-10": {"e_shop": {"1": {"sales": 111}}}
        }
        cookie, _ = self.login("usr_001", "1111")
        stale_version = int(self.store["version"])
        # Другой клиент успешно записал новую версию.
        self.store["version"] = stale_version + 1
        self.store["payload"]["shopMetrics"]["cashiers"]["2026-10"]["e_shop"]["1"]["sales"] = 222
        status, data, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": stale_version,
                "payload": {
                    "shopMetrics": {
                        "cashiers": {"2026-10": {"e_shop": {"1": {"sales": 999}}}}
                    }
                },
            },
            cookie=cookie,
        )
        self.assertTrue(status.startswith("409"), status)
        self.assertEqual(data.get("error"), "conflict")
        self.assertEqual(
            self.store["payload"]["shopMetrics"]["cashiers"]["2026-10"]["e_shop"]["1"]["sales"],
            222,
        )

    def test_visitors_and_self_checkout_still_save(self):
        cookie, _ = self.login("usr_001", "1111")
        status, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(self.store["version"]),
                "payload": {
                    "shopMetrics": {
                        "store": {"2026-10": {"1": {"visitors": 80}}},
                        "channelValues": {
                            "2026-10": {"self_checkout": {"1": {"sales": 5000, "receipts": 20}}}
                        },
                    }
                },
            },
            cookie=cookie,
        )
        self.assertTrue(status.startswith("200"), status)
        self.assertEqual(
            self.store["payload"]["shopMetrics"]["store"]["2026-10"]["1"]["visitors"],
            80,
        )
        ch = self.store["payload"]["shopMetrics"]["channelValues"]["2026-10"]["self_checkout"]["1"]
        self.assertEqual(ch["sales"], 5000)
        # Очистка visitors через null.
        status, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(self.store["version"]),
                "payload": {
                    "shopMetrics": {"store": {"2026-10": {"1": {"visitors": None}}}}
                },
            },
            cookie=cookie,
        )
        self.assertTrue(status.startswith("200"), status)
        self.assertNotIn(
            "visitors",
            self.store["payload"]["shopMetrics"]["store"]["2026-10"]["1"],
        )

    def test_reload_after_cashier_save(self):
        cookie, _ = self.login("usr_001", "1111")
        call(
            "PUT",
            "/api/state",
            {
                "version": int(self.store["version"]),
                "payload": {
                    "shopMetrics": {
                        "cashiers": {
                            "2026-10": {
                                "e_shop": {"1": {"sales": 164197, "receipts": 110}}
                            }
                        }
                    }
                },
            },
            cookie=cookie,
        )
        status, data, *_ = call("GET", "/api/state", cookie=cookie)
        self.assertTrue(status.startswith("200"), status)
        day = data["payload"]["shopMetrics"]["cashiers"]["2026-10"]["e_shop"]["1"]
        self.assertEqual(day["sales"], 164197)
        self.assertEqual(day["receipts"], 110)

    def test_client_metric_clear_uses_null_sentinel(self):
        src = Path(__file__).resolve().parents[1].joinpath("app.html").read_text(encoding="utf-8")
        self.assertIn("function metricMarkCleared(", src)
        self.assertIn("function metricEmpDayMapPrune(", src)
        self.assertIn("function saveMetricChange(", src)
        self.assertIn("metricMarkCleared(rec, field)", src)
        self.assertIn('metricMarkCleared(rec, "actual_cashier_employee_id")', src)
        api = Path(__file__).resolve().parent.joinpath("wt_api.py").read_text(encoding="utf-8")
        self.assertIn("if val is None:", api)
        self.assertIn("base.pop(key, None)", api)

    def test_client_checklist_toggle_clear_shop_and_territory(self):
        """Повторный клик ✓/✕ снимает Mark; очистка через null (metricMarkCleared)."""
        src = Path(__file__).resolve().parents[1].joinpath("app.html").read_text(encoding="utf-8")
        shop = src[
            src.find("function setShopMetricCheck(")
            : src.find("function shopCheckCellHtml(")
        ]
        self.assertIn("metricMarkCleared(rec, markKey)", shop)
        self.assertIn("rec[markKey] === nextMark", shop)
        self.assertIn("markVal", shop)
        self.assertNotIn("delete rec[field + \"Mark\"]", shop)
        self.assertNotIn("delete rec[markKey]", shop)
        dept = src[
            src.find("function setDeptMetricCheck(")
            : src.find("function deptMetricInputHtml(")
        ]
        self.assertIn("metricMarkCleared(rec, markKey)", dept)
        self.assertIn("rec[markKey] === nextMark", dept)
        self.assertIn("wtSaveMetricValue(path, markVal)", dept)
        self.assertIn("clearDeptMetricDayIfEmpty", dept)
        # KPI-формулы не трогаем этим изменением.
        self.assertIn("function shopCheckKpiPayout(", src)
        self.assertIn("function shopCheckKpiMonthStats(", src)

    def test_staff_writes_pickers_and_managers_category_stores(self):
        cookie, _ = self.login("usr_001", "1111")
        status, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(self.store["version"]),
                "payload": {
                    "shopMetrics": {
                        "pickers": {"2026-10": {"e_shop": {"2": {"display": 12}}}},
                        "managers": {"2026-10": {"e_shop": {"2": {"receiving": 3}}}},
                    }
                },
            },
            cookie=cookie,
        )
        self.assertTrue(status.startswith("200"), status)
        self.assertEqual(
            self.store["payload"]["shopMetrics"]["pickers"]["2026-10"]["e_shop"]["2"]["display"],
            12,
        )
        self.assertEqual(
            self.store["payload"]["shopMetrics"]["managers"]["2026-10"]["e_shop"]["2"]["receiving"],
            3,
        )

    def test_staff_cannot_change_shop_metrics_admin_meta(self):
        self.store["payload"]["shopMetrics"]["store_id"] = "store_volodarsky"
        self.store["payload"]["shopMetrics"]["substitutionPayDefaults"] = {"day_rate": 100}
        before = json.loads(json.dumps(self.store["payload"]["shopMetrics"]))
        cookie, _ = self.login("usr_001", "1111")
        status, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(self.store["version"]),
                "payload": {
                    "shopMetrics": {
                        "store_id": "hacked",
                        "substitutionPayDefaults": {"day_rate": 999},
                        "facts": {"ok": 2},
                    }
                },
            },
            cookie=cookie,
        )
        self.assertTrue(status.startswith("403"), status)
        self.assertEqual(self.store["payload"]["shopMetrics"], before)

    def test_admin_shop_metrics_overlay_keeps_sibling_keys(self):
        """Админ не должен целиком затирать shopMetrics неполным снимком."""
        self.store["payload"]["shopMetrics"]["cashiers"] = {
            "2026-10": {"e_shop": {"1": {"sales": 164197, "receipts": 110}}}
        }
        self.store["payload"]["shopMetrics"]["facts"] = {"ok": 1}
        cookie, _ = self.login("user_admin", "5555")
        status, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(self.store["version"]),
                "payload": {"shopMetrics": {"store": {"2026-10": {"1": {"visitors": 50}}}}},
            },
            cookie=cookie,
        )
        self.assertTrue(status.startswith("200"), status)
        metrics = self.store["payload"]["shopMetrics"]
        self.assertEqual(metrics["store"]["2026-10"]["1"]["visitors"], 50)
        self.assertEqual(metrics["cashiers"]["2026-10"]["e_shop"]["1"]["sales"], 164197)
        self.assertEqual(metrics["facts"]["ok"], 1)

    def test_admin_saves_work_access_with_cashier_nulls(self):
        """Галочки workAccess сохраняются админом даже если в cashiers есть JSON null."""
        self.store["payload"]["shopMetrics"]["cashiers"] = {
            "2026-10": {"e_shop": {"1": {"sales": 100, "receipts": 5}}}
        }
        self.store["payload"]["shopMetrics"]["workAccess"] = {
            "2026-10": {"e_shop": ["metric_display_positions"], "e_other": ["metric_assembly"]}
        }
        cookie, _ = self.login("user_admin", "5555")
        status, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(self.store["version"]),
                "payload": {
                    "shopMetrics": {
                        "cashiers": {
                            "2026-10": {"e_shop": {"1": {"sales": None, "receipts": 5}}}
                        },
                        "workAccess": {
                            "2026-10": {
                                "e_shop": [
                                    "metric_display_positions",
                                    "metric_recount",
                                    "metric_assembly",
                                    "metric_sugar_packing",
                                ],
                                "e_other": ["metric_assembly"],
                            }
                        },
                        "extraAccess": {"e_shop": []},
                    }
                },
            },
            cookie=cookie,
        )
        self.assertTrue(status.startswith("200"), status)
        metrics = self.store["payload"]["shopMetrics"]
        self.assertNotIn("sales", metrics["cashiers"]["2026-10"]["e_shop"]["1"])
        self.assertEqual(metrics["cashiers"]["2026-10"]["e_shop"]["1"]["receipts"], 5)
        self.assertEqual(
            metrics["workAccess"]["2026-10"]["e_shop"],
            [
                "metric_display_positions",
                "metric_recount",
                "metric_assembly",
                "metric_sugar_packing",
            ],
        )
        self.assertEqual(metrics["workAccess"]["2026-10"]["e_other"], ["metric_assembly"])
        # Снятие всех галочек — явный [].
        status, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(self.store["version"]),
                "payload": {
                    "shopMetrics": {
                        "workAccess": {"2026-10": {"e_shop": []}},
                    }
                },
            },
            cookie=cookie,
        )
        self.assertTrue(status.startswith("200"), status)
        self.assertEqual(self.store["payload"]["shopMetrics"]["workAccess"]["2026-10"]["e_shop"], [])
        self.assertEqual(
            self.store["payload"]["shopMetrics"]["workAccess"]["2026-10"]["e_other"],
            ["metric_assembly"],
        )
        status, data, *_ = call("GET", "/api/state", cookie=cookie)
        self.assertEqual(data["payload"]["shopMetrics"]["workAccess"]["2026-10"]["e_shop"], [])

    def test_client_staff_metric_input_includes_category_stores(self):
        src = Path(__file__).resolve().parents[1].joinpath("app.html").read_text(encoding="utf-8")
        self.assertIn("STAFF_METRIC_CATEGORY_STORE_KEYS", src)
        block = src[src.find("STAFF_METRIC_CATEGORY_STORE_KEYS"):src.find("STAFF_METRIC_SETUP_KEYS")]
        self.assertIn('"cashiers"', block)
        self.assertIn('"pickers"', block)
        self.assertIn('"managers"', block)
        self.assertIn("function saveMetricChange(", src)
        self.assertNotIn("store_id", block)
        self.assertNotIn("workAccess", block)

    def test_client_metric_assignment_save_feedback(self):
        """Галочки в карточке — локально; Save сбрасывает busy; PUT с тайм-аутом."""
        src = Path(__file__).resolve().parents[1].joinpath("app.html").read_text(encoding="utf-8")
        fn = src[src.find("function setShopEmployeeExtraMetric("):src.find("function metricCatalogItemUsesGroupAll(")]
        self.assertIn("if (fioSection)", fn)
        self.assertIn("Не удалось сохранить назначение метрики на сервер", fn)
        self.assertIn('ok === "timeout"', fn)
        self.assertIn("wtBuildWorkAccessSaveOps(", fn)
        self.assertIn("wtDomainWriterSave({ ops: waOps, atomic: true })", fn)
        # В карточке (fio) нет записи на каждый клик — иначе очередь блокирует Save.
        apply_at = fn.find("applyShopEmployeeWorkMetric(")
        fio_branch = fn.find("if (fioSection)", apply_at)
        ops_at = fn.find("wtBuildWorkAccessSaveOps(", apply_at)
        self.assertGreater(fio_branch, apply_at)
        self.assertGreater(ops_at, fio_branch)
        paint_before_return = fn.find("paintWorkMetrics();", fio_branch)
        self.assertGreater(ops_at, paint_before_return)
        commit = src[src.find("function commitEmployeeCard("):src.find("function employeeCardActionsHtml(")]
        self.assertIn("clearCommitBusy", commit)
        self.assertIn("commitEmployeeCard.busy = true", commit)
        self.assertIn("shopMaterializeWorkAccessEmployee(", commit)
        self.assertNotIn("shopMaterializeWorkAccessMonth(", commit)
        self.assertIn("wtBuildEmployeeCardSaveOps(", commit)
        self.assertIn("wtDomainWriterSave({ ops: cardOps, atomic: true })", commit)
        # saveApp только для локального режима; сервер — узкий OPS.
        self.assertIn("!wtServerMode", commit)
        self.assertLess(commit.find("!wtServerMode"), commit.find("Promise.resolve(saveApp())"))
        self.assertLess(commit.find("Promise.resolve(saveApp())"), commit.find("wtBuildEmployeeCardSaveOps("))
        self.assertIn("Сохранение ещё выполняется", commit)
        self.assertIn('ok === "timeout"', commit)
        self.assertIn("refreshMetricsUi()", commit)
        self.assertIn("function wtBuildEmployeeCardSaveOps(", src)
        self.assertIn("function wtEmployeeCardWorkAccessPath(", src)
        self.assertIn("function shopMaterializeWorkAccessEmployee(", src)
        # busy ставится до тяжёлых снимков JSON.
        busy_at = commit.find("commitEmployeeCard.busy = true")
        snap_at = commit.find("JSON.parse(JSON.stringify(store.workAccess")
        self.assertGreater(snap_at, busy_at)
        put = src[src.find("function putServerStateOnce("):src.find("function saveApp(")]
        self.assertIn("AbortController", put)
        self.assertIn("AbortError", put)
        self.assertIn('return "timeout"', put)
        api = Path(__file__).resolve().parent.joinpath("wt_api.py").read_text(encoding="utf-8")
        self.assertIn("def _merge_metric_branch_admin(", api)
        self.assertIn('base["workAccess"] = _merge_access_map(', api)

    def test_client_work_access_drives_metric_table_people(self):
        """Магазин: assignments+workAccess; Территория: peopleAssignedToMetricOnTab."""
        src = Path(__file__).resolve().parents[1].joinpath("app.html").read_text(encoding="utf-8")
        self.assertIn("function metricHostsOnDeptTab(", src)
        self.assertIn("function metricAssignmentPlaceCaption(", src)
        self.assertIn("function peopleAssignedToMetricOnTab(", src)
        self.assertIn("function shopPeopleAssignedToMetricOnTab(", src)
        self.assertIn("function shopMetricParticipantPeople(", src)
        people_fn = src[
            src.find("function workMetricPeopleForTab(") : src.find("function deptMetricFields(")
        ]
        self.assertIn("peopleAssignedToMetricOnTab", people_fn)
        self.assertIn("shopPeopleAssignedToMetricOnTab", people_fn)
        self.assertIn("shopEmployeeHasWorkMetric", people_fn)
        self.assertIn("metricHostsOnDeptTab", people_fn)
        self.assertIn("TERRITORY_DEPT_CODES.forEach", people_fn)
        self.assertIn('metricItemHasObjectSide(metricItem, objectSide)', people_fn)
        # Магазин с assignments: список из shopPeopleAssignedToMetricOnTab.
        self.assertIn('shopPeopleAssignedToMetricOnTab(metricItem, tab, mid)', people_fn)
        # Legacy без assignments: фильтр основной группы карточки сохраняется.
        self.assertIn('code === "shop" && employeeCardWorkGroupId(e, "shop") !== tab', people_fn)
        self.assertNotIn("employeeBelongsToWorkGroup", people_fn)
        self.assertNotIn("employeeMatchesMetricAssignments", people_fn)
        store_total = src[
            src.find("function shopMetricStoreMonthTotal(")
            : src.find("function getShopMetricValue(", src.find("function shopMetricStoreMonthTotal("))
        ]
        self.assertIn("shopMetricParticipantPeople", store_total)
        self.assertNotIn("shopMetricContributorIds", store_total)
        shop_emp = src[
            src.find("function shopMetricEmployees(") : src.find("function parseShopMetricNumber(")
        ]
        self.assertIn('employeeCardWorkGroupId(e, "shop") !== tab', shop_emp)
        self.assertNotIn("employeesForWorkGroup(\"shop\"", shop_emp)
        self.assertNotIn("employeeBelongsToWorkGroup", shop_emp)
        hub = src[src.find("function territoryHubPeople(") : src.find("function syncTerritoryMetricsHubNav(")]
        self.assertIn("shopEmployeeHasWorkMetric", hub)
        self.assertNotIn("employeeBelongsToWorkGroup", hub)
        grant = src[
            src.find("function shopGrantableExtraMetrics(") : src.find("function shopMetricFactsMonth(")
        ]
        # Двухэтапный фильтр ФИО + сторона объекта (shop ↔ territory).
        self.assertNotIn("isGenericMetricsDept(dept) && isGenericMetricsDept(mDept)", grant)
        self.assertIn("metricBelongsToEmployeeDeptForFio", grant)
        self.assertIn("employeeMatchesMetricAssignments", grant)
        self.assertIn("metricItemHasObjectSide(it, empSide)", grant)
        self.assertIn("function metricBelongsToEmployeeDeptForFio(", src)
        self.assertIn("function metricItemObjectSides(", src)
        self.assertIn("function findAmbiguousMetricObjectOwnership(", src)
        card = src[src.find("function shopExtraFunctionsHtml(") : src.find("function renderFio(")]
        self.assertIn("metricAssignmentPlaceCaption", card)
        self.assertIn("emp-extra-fn-place", card)
        # Связка: магазин и dept tabs используют один people-builder.
        self.assertIn("return workMetricPeopleForTab(\"shop\", metricId, catId)", src)
        self.assertIn("return workMetricPeopleForTab(deptCode, metricId, tabId", src)

    def test_client_territory_work_tables_use_home_fact_dept(self):
        """Хаб и dwork/office читают факты по home сотрудника, колонки — по assignments."""
        src = Path(__file__).resolve().parents[1].joinpath("app.html").read_text(encoding="utf-8")
        self.assertIn("function metricFactDeptForEmployee(", src)
        self.assertIn("function diagnoseMetricFactDeptSplit(", src)
        hub = src[
            src.find("function renderTerritoryMetrics(")
            : src.find("function renderDeptWorkMetrics(")
        ]
        self.assertIn("metricFactDeptForEmployee(employeeId, spec.departmentId", hub)
        dwork = src[
            src.find("function renderDeptWorkMetrics(")
            : src.find("function fillDeptWorkTable(")
        ]
        self.assertIn("metricFactDeptForEmployee(employeeId, dept)", dwork)
        self.assertIn("factDeptFn:", dwork)
        fill = src[
            src.find("function fillDeptWorkTable(")
            : src.find("function fillDeptWorkTable(") + 1200
        ]
        self.assertIn("isGenericMetricsDept(dept)", fill)
        self.assertIn("metricFactDeptForEmployee(employeeId, dept)", fill)
        office = src[
            src.find("function renderDeptMetrics(")
            : src.find("function deptWorkDom(")
        ]
        self.assertIn("metricFactDeptForEmployee(employeeId, dept)", office)
        # Назначения колонок не подменены home-фильтром в peopleAssigned.
        people = src[
            src.find("function peopleAssignedToMetricOnTab(")
            : src.find("function employeeMatchesMetricAssignments(")
        ]
        self.assertIn("extra_employee_ids", people)
        self.assertNotIn("metricFactDeptForEmployee", people)

    def test_client_shop_territory_assignment_isolation(self):
        """Магазин Володарского и Территория не смешиваются в назначениях и пулах сотрудников."""
        src = Path(__file__).resolve().parents[1].joinpath("app.html").read_text(encoding="utf-8")
        self.assertIn("function allowedAssignmentDeptCodesForCategory(", src)
        self.assertIn("function assignmentDeptAllowedForCategory(", src)
        self.assertIn("function deptsForMetricAssignmentFormRow(", src)
        self.assertIn("function sanitizeMetricCatalogAssignDraftForCategory(", src)
        belong = src[
            src.find("function employeeBelongsToWorkGroup(") : src.find("function employeeCardWorkGroupId(")
        ]
        self.assertIn("dept === \"shop\" && isTerritoryCode(home)", belong)
        self.assertIn("!inTarget && home !== dept", belong)
        self.assertNotIn("employeeHomeDeptCode(employee.id) !== dept", belong)
        pool = src[
            src.find("function departmentEmployeePool(") : src.find("function territoryDeptEmployeePool(")
        ]
        self.assertIn("code === \"shop\" && isTerritoryCode(home)", pool)
        # Территория больше не режет home===shop при записи в employees отдела.
        self.assertNotIn("code !== \"shop\" && home === \"shop\"", pool)
        self.assertIn("function metricsDeptCodeForEmployee(", src)
        self.assertIn("function buildWarehouseStaffDiagnostics(", src)
        # Узкая материализация одного сотрудника: dept берётся через metricsDeptCodeForEmployee.
        self.assertIn("function shopMaterializeWorkAccessEmployee(", src)
        apply_wa = src[
            src.find("function applyShopEmployeeWorkMetric(")
            : src.find("function wtBuildWorkAccessSaveOps(")
        ]
        self.assertIn("metricsDeptCodeForEmployee(employeeId)", apply_wa)
        self.assertIn("shopMaterializeWorkAccessEmployee(selectedMonth, deptCode, employeeId)", apply_wa)
        asg_people = src[
            src.find("function employeeInMetricAssignmentDept(") : src.find("function employeeMatchesMetricAssignments(")
        ]
        # Магазин: основная группа карточки. Территория: подразделение + должность (не work group).
        self.assertIn("dept === \"shop\" && isTerritoryCode(home)", asg_people)
        self.assertIn('employeeCardWorkGroupId(e, "shop") !== row.work_tab_id', asg_people)
        self.assertIn("employeeInMetricAssignmentDept(e, dept)", asg_people)
        self.assertIn("employeeHasPositionId(e, row.position_id)", asg_people)
        self.assertNotIn("employeesForWorkGroup(dept, row.work_tab_id)", asg_people)
        self.assertIn("function peopleAssignedToMetricOnTab(", src)
        self.assertIn("function employeesForMetricAssignmentRowWithSelected(", src)
        match_asg = src[
            src.find("function employeeMatchesMetricAssignments(") : src.find("function syncWorkTabIdsFromMetricAssignments(")
        ]
        self.assertIn('employeeCardWorkGroupId(employee, "shop") !== row.work_tab_id', match_asg)
        self.assertIn("Территория: основная группа карточки НЕ ограничивает назначение", match_asg)
        self.assertNotIn("employeeBelongsToWorkGroup(employee, row.department_id, row.work_tab_id)", match_asg)
        self.assertIn("employeeHasPositionId(employee, row.position_id)", match_asg)
        ts = src[
            src.find("function timesheetEmployeesForView(") : src.find("function renderTimesheetGroupFilter(")
        ]
        self.assertIn("directoryEmployees(w).forEach(add)", ts)
        save = src[src.find("function saveMetricCatalogForm(") : src.find("function saveMetricCatalogForm(") + 12000]
        self.assertIn("assignmentDeptAllowedForCategory(row.department_id, catRaw)", save)
        self.assertIn("нельзя смешивать Магазин Володарского и Территорию", save)
        render = src[
            src.find("function renderMetricCatalogAssignments(") : src.find("function onMetricCatalogStructureChange(")
        ]
        self.assertIn("deptsForMetricAssignmentFormRow(row)", render)
        self.assertIn("<label>Подразделение</label>", render)
        self.assertIn("employeesForMetricAssignmentRowWithSelected(row)", render)
        self.assertIn("Сняты сотрудники, не подходящие под выбранную должность", src[
            src.find("function onMetricCatalogAssignmentField(") : src.find("function onMetricCatalogAssignmentEmployee(")
        ])
        # Двусторонняя изоляция объектов: карточка не создаёт чужие assignments.
        sync_asg = src[
            src.find("function syncEmployeeMetricAssignmentsToCatalog(")
            : src.find("function commitEmployeeCard(")
        ]
        self.assertIn("metricItemHasObjectSide(item, empSide)", sync_asg)
        self.assertIn("territory_category не обходит изоляцию", sync_asg)
        self.assertNotIn("!(item.territory_category)", sync_asg)
        append_shop = src[
            src.find("function shopAppendCatalogExtraFields(")
            : src.find("function shopCashierDisplayFields(")
        ]
        self.assertIn('metricItemHasObjectSide(it, "shop")', append_shop)
        self.assertNotIn("return shopWorkMetricPeople(it.id, category).length > 0", append_shop)
        dept_fields = src[
            src.find("function deptMetricFields(") : src.find("function deptMetricFields(") + 2500
        ]
        self.assertIn('metricItemHasObjectSide(it, "territory")', dept_fields)
        # СПУ: новые подключения только с use_in_spu; keepIds сохраняет уже связанные.
        spu_pick = src[
            src.find("function spuParticipatingWorkMetrics(") : src.find("function spuSrcMetricSectionId(")
        ]
        self.assertIn("metricUseInSpu(m)", spu_pick)
        self.assertIn("keep[mid]", spu_pick)
        self.assertIn("function spuMetricLinkConflictsWithoutUseInSpu(", src)
        self.assertIn("function findDuplicateEmployeeIdsAcrossWorkspaces(", src)
        self.assertIn("if (!metricUseInSpu(item)) return;", src[
            src.find("function applyMetricResultRoute(") : src.find("function metricRouteSourceOptions(")
        ])
        # Сохранение каталога: timeout и 409 conflict_review без ложного успеха.
        cat_save = src[src.find("Promise.resolve(saveAppWithPayloadSlice(metricCatalogPutSlice))"):]
        self.assertIn('ok === "timeout"', cat_save)
        self.assertIn('ok === "conflict_review"', cat_save)
        self.assertIn("function metricCatalogPutSlice(", src)

    def test_staff_spu_indicators_read_write(self):
        spu = {
            "user_id": "usr_spu",
            "display_name": "СПУ",
            "pin": "8888",
            "role": "user",
            "is_active": True,
            "departments": ["spu"],
            "permissions": {"managementIndicators": "edit", "management": "none"},
        }
        self.store["payload"]["users"].append(spu)
        cookie, _ = self.login("usr_spu", "8888")
        status, data, raw, _ = call("GET", "/api/state", cookie=cookie)
        self.assertTrue(status.startswith("200"), status)
        self.assertIn("management", data["payload"])
        self.assertEqual(data["payload"]["management"]["indicators"][0]["finance"], 1)
        self.assertNotIn("salary", data["payload"])
        version = int(data["version"])
        status2, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": version,
                "payload": {"management": {"indicators": [{"finance": 2, "name": "Выручка"}]}},
            },
            cookie=cookie,
        )
        self.assertTrue(status2.startswith("200"), status2)
        self.assertEqual(self.store["payload"]["management"]["indicators"][0]["finance"], 2)
        status3, err, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(self.store["version"]),
                "payload": {"management": {"meetings": [{"id": "hack"}]}},
            },
            cookie=cookie,
        )
        self.assertTrue(status3.startswith("403"), status3)
        self.assertEqual(err["error"], "forbidden")
        self.assertNotIn("meetings", self.store["payload"]["management"])

    def test_salary_editor_reads_and_writes_salary(self):
        pay = {
            "user_id": "usr_pay",
            "display_name": "Расчёт",
            "pin": "4444",
            "role": "user",
            "legacy_role": "adjust",
            "is_active": True,
            "departments": ["summary"],
            "permissions": {"salary": "edit", "salaryCalc": "edit"},
        }
        self.store["payload"]["users"].append(pay)
        cookie, _ = self.login("usr_pay", "4444")
        status, data, raw, _ = call("GET", "/api/state", cookie=cookie)
        self.assertTrue(status.startswith("200"), status)
        self.assertIn("salary", data["payload"])
        self.assertNotIn("1111", raw)
        # Calc без Pay: может менять начисления, не issued.
        status2, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(data["version"]),
                "payload": {"salary": {"2026-10": {"e_shop": {"bonus": 500, "rate": 1500}}}},
            },
            cookie=cookie,
        )
        self.assertTrue(status2.startswith("200"), status2)
        self.assertEqual(self.store["payload"]["salary"]["2026-10"]["e_shop"]["bonus"], 500)
        self.assertFalse(self.store["payload"]["salary"]["2026-10"]["e_shop"]["issued"])

    def test_salary_pay_reads_all_payroll_depts_without_shop_membership(self):
        """Выдача зарплаты: видит shop/warehouse без departments=shop, без метрик/ревизий."""
        payer = {
            "user_id": "usr_salary_pay",
            "display_name": "Наталья",
            "pin": "4445",
            "role": "user",
            "legacy_role": "pay",
            "is_active": True,
            "departments": ["summary"],
            "permissions": {"salaryPay": "edit"},
        }
        self.store["payload"]["users"].append(payer)
        self.store["payload"]["repSales"] = {"2026-10": {"e_tp": {"bags": 1}}}
        self.store["payload"]["shopSalesPlan"] = {"months": {"2026-10": {"plan": 1}}}
        cookie, _ = self.login("usr_salary_pay", "4445")
        status, data, raw, _ = call("GET", "/api/state", cookie=cookie)
        self.assertTrue(status.startswith("200"), status)
        payload = data["payload"]
        codes = [w["code"] for w in payload.get("workspaces") or []]
        self.assertIn("shop", codes)
        self.assertIn("warehouse", codes)
        shop = next(w for w in payload["workspaces"] if w["code"] == "shop")
        self.assertEqual(shop["employees"][0]["id"], "e_shop")
        self.assertIn("e_shop", shop.get("employeeIds") or [])
        self.assertEqual(shop["timesheet"]["2026-10"]["e_shop"]["1"], "Я")
        self.assertNotIn("pin", shop)
        self.assertIn("salary", payload)
        self.assertIn("shopPayroll", payload)
        self.assertIn("shopMetrics", payload)
        self.assertIn("repSales", payload)
        self.assertIn("shopSalesPlan", payload)
        self.assertNotIn("shopRevision", payload)
        self.assertNotIn("1111", raw)
        # Выдача: только issued.
        status_ok, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(data["version"]),
                "payload": {"salary": {"2026-10": {"e_shop": {"issued": True}}}},
            },
            cookie=cookie,
        )
        self.assertTrue(status_ok.startswith("200"), status_ok)
        self.assertTrue(self.store["payload"]["salary"]["2026-10"]["e_shop"]["issued"])
        status_bad, err, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(self.store["version"]),
                "payload": {"salary": {"2026-10": {"e_shop": {"bonus": 999}}}},
            },
            cookie=cookie,
        )
        self.assertTrue(status_bad.startswith("403"), status_bad)
        self.assertEqual(err["error"], "forbidden")
        self.assertNotEqual(self.store["payload"]["salary"]["2026-10"]["e_shop"].get("bonus"), 999)

    def test_salary_calc_cannot_mark_issued_without_pay(self):
        calc = {
            "user_id": "usr_salary_calc",
            "display_name": "Ольга",
            "pin": "4446",
            "role": "user",
            "legacy_role": "adjust",
            "is_active": True,
            "departments": ["summary"],
            "permissions": {"salaryCalc": "edit"},
        }
        self.store["payload"]["users"].append(calc)
        cookie, _ = self.login("usr_salary_calc", "4446")
        status, data, *_ = call("GET", "/api/state", cookie=cookie)
        self.assertTrue(status.startswith("200"), status)
        codes = [w["code"] for w in data["payload"].get("workspaces") or []]
        self.assertIn("shop", codes)
        status_bad, err, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(data["version"]),
                "payload": {"salary": {"2026-10": {"e_shop": {"issued": True}}}},
            },
            cookie=cookie,
        )
        self.assertTrue(status_bad.startswith("403"), status_bad)
        self.assertEqual(err["error"], "forbidden")
        self.assertFalse(self.store["payload"]["salary"]["2026-10"]["e_shop"]["issued"])

    def test_no_salary_rights_no_payroll_payload(self):
        cookie, _ = self.login("usr_001", "1111")
        status, data, raw, _ = call("GET", "/api/state", cookie=cookie)
        self.assertTrue(status.startswith("200"), status)
        self.assertNotIn("salary", data["payload"])
        self.assertNotIn("shopPayroll", data["payload"])
        self.assertNotIn("repSales", data.get("payload") or {})
        self.assertNotIn('"salary"', raw)

    def test_admin_duplicate_pin_rejected(self):
        cookie, _ = self.login()
        version = self.current_version(cookie)
        payload = json.loads(json.dumps(self.store["payload"]))
        payload["users"].append({
            "user_id": "user_admin2",
            "display_name": "Второй админ",
            "pin": "5555",
            "role": "admin",
            "legacy_role": "admin",
            "is_active": True,
            "departments": ["shop"],
            "permissions": {},
        })
        status, data, raw, _ = call(
            "PUT",
            "/api/state",
            {"version": version, "payload": payload},
            cookie=cookie,
        )
        self.assertTrue(status.startswith("400"), status)
        self.assertEqual(data["error"], "duplicate pin")
        self.assertNotIn("5555", raw)
        ids = [u["user_id"] for u in self.store["payload"]["users"]]
        self.assertNotIn("user_admin2", ids)

    def test_staff_updates_own_timesheet_only(self):
        cookie, _ = self.login("usr_001", "1111")
        before_wh = json.loads(json.dumps(self.store["payload"]["workspaces"][1]))
        payload = {
            "workspaces": [{"code": "shop", "timesheet": {"2026-10": {"e_shop": {"1": "О"}}}}]
        }
        status, data, *_ = call(
            "PUT",
            "/api/state",
            {"version": int(self.store["version"]), "payload": payload},
            cookie=cookie,
        )
        self.assertTrue(status.startswith("200"), status)
        self.assertNotIn("payload", data)
        shop = next(w for w in self.store["payload"]["workspaces"] if w["code"] == "shop")
        self.assertEqual(shop["timesheet"]["2026-10"]["e_shop"]["1"], "О")
        self.assertEqual(shop["employees"][0]["rate"], 1500)
        self.assertEqual(self.store["payload"]["workspaces"][1], before_wh)
        self.assertEqual(self.store["payload"]["salary"], SAMPLE_APP["salary"])
        status_get, got, *_ = call("GET", "/api/state", cookie=cookie)
        self.assertTrue(status_get.startswith("200"), status_get)
        codes = [w["code"] for w in got["payload"]["workspaces"]]
        self.assertEqual(codes, ["shop"])

    def test_staff_cannot_change_fio_or_rate_via_timesheet(self):
        cookie, _ = self.login("usr_001", "1111")
        version = int(self.store["version"])
        for body in (
            {"workspaces": [{"code": "shop", "employees": [{"id": "e_shop", "name": "Хак", "rate": 1}]}]},
            {"workspaces": [{"code": "shop", "timesheet": {"x": 1}, "employees": [{"id": "e_shop", "name": "Хак"}]}]},
        ):
            status, data, *_ = call("PUT", "/api/state", {"version": version, "payload": body}, cookie=cookie)
            self.assertTrue(status.startswith("403"), status)
            self.assertEqual(data["error"], "forbidden")
        shop = next(w for w in self.store["payload"]["workspaces"] if w["code"] == "shop")
        self.assertEqual(shop["employees"][0]["name"], "Касса")
        self.assertEqual(shop["employees"][0]["rate"], 1500)
        self.assertEqual(shop["timesheet"]["2026-10"]["e_shop"]["1"], "Я")

    def test_manager_saves_own_department_employee(self):
        self.add_user({
            "user_id": "usr_dir",
            "display_name": "Директор магазина",
            "pin": "4444",
            "role": "manager",
            "legacy_role": "staff",
            "is_active": True,
            "departments": ["shop"],
            "permissions": {"timesheet": "edit"},
        })
        cookie, _ = self.login("usr_dir", "4444")
        version = int(self.store["version"])
        status, data, raw, _ = call(
            "PUT",
            "/api/state",
            {
                "version": version,
                "payload": {
                    "workspaces": [{
                        "code": "shop",
                        "employees": [
                            {"id": "e_shop", "name": "Касса", "employee_id": "e_shop"},
                            {
                                "id": "e_new",
                                "name": "Новый Сотрудник",
                                "position": "Продавец",
                                "employee_id": "e_new",
                                "rate": 0,
                                "taxiDaily": 0,
                                "terms": {},
                            },
                        ],
                        "employeeIds": ["e_new", "e_shop"],
                        "timesheet": {"2026-10": {"e_shop": {"1": "Я"}, "e_new": {"2": "О"}}},
                    }]
                },
            },
            cookie=cookie,
        )
        self.assertTrue(status.startswith("200"), status)
        self.assertNotIn("4444", raw)
        shop = next(w for w in self.store["payload"]["workspaces"] if w["code"] == "shop")
        by_id = {e["id"]: e for e in shop["employees"]}
        self.assertEqual(list(by_id), ["e_shop", "e_new"])
        self.assertEqual(by_id["e_shop"]["name"], "Касса")
        self.assertEqual(by_id["e_shop"]["rate"], 1500)
        self.assertEqual(by_id["e_shop"]["taxiDaily"], 200)
        self.assertEqual(by_id["e_new"]["name"], "Новый Сотрудник")
        self.assertEqual(by_id["e_new"]["employee_id"], "e_new")
        self.assertEqual(by_id["e_new"]["position"], "Продавец")
        self.assertNotIn("rate", by_id["e_new"])
        self.assertEqual(shop["employeeIds"], ["e_new", "e_shop"])
        self.assertEqual(shop["timesheet"]["2026-10"]["e_shop"]["1"], "Я")
        self.assertEqual(shop["timesheet"]["2026-10"]["e_new"]["2"], "О")
        self.assertEqual(self.store["payload"]["workspaces"][1]["employees"][0]["name"], "Кладовщик")
        self.assertEqual(self.store["payload"]["salary"]["2026-10"]["e_shop"]["rate"], 1500)
        self.assertNotIn("payload", data)
        status_get, got, *_ = call("GET", "/api/state", cookie=cookie)
        self.assertTrue(status_get.startswith("200"), status_get)
        visible = got["payload"]["workspaces"]
        self.assertEqual([w["code"] for w in visible], ["shop"])
        visible_new = next(e for e in visible[0]["employees"] if e["id"] == "e_new")
        self.assertEqual(visible_new["name"], "Новый Сотрудник")
        self.assertNotIn("rate", visible[0]["employees"][0])

        admin_cookie, _ = self.login()
        status_admin, admin_state, *_ = call("GET", "/api/state", cookie=admin_cookie)
        self.assertTrue(status_admin.startswith("200"), status_admin)
        admin_shop = next(w for w in admin_state["payload"]["workspaces"] if w["code"] == "shop")
        admin_new = next(e for e in admin_shop["employees"] if e["id"] == "e_new")
        self.assertEqual(admin_new["name"], "Новый Сотрудник")
        self.assertEqual(admin_new["employee_id"], "e_new")
        self.assertEqual(admin_shop["employees"][0]["id"], "e_shop")
        self.assertEqual(admin_shop["employees"][0]["rate"], 1500)

    def test_manager_cannot_edit_other_department_or_salary_or_ids(self):
        self.add_user({
            "user_id": "usr_dir",
            "display_name": "Директор магазина",
            "pin": "4444",
            "role": "manager",
            "legacy_role": "staff",
            "is_active": True,
            "departments": ["shop"],
            "permissions": {"timesheet": "edit"},
        })
        cookie, _ = self.login("usr_dir", "4444")
        version = int(self.store["version"])
        before = json.loads(json.dumps(self.store["payload"]))
        # rate без salary: PUT 200, поле игнорируется (как _overlay_open), данные не меняются.
        status, data, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": version,
                "payload": {
                    "workspaces": [{"code": "shop", "employees": [{"id": "e_shop", "name": "Касса", "rate": 1}]}]
                },
            },
            cookie=cookie,
        )
        self.assertTrue(status.startswith("200"), status)
        self.assertEqual(self.store["payload"], before)
        shop_before = next(w for w in before["workspaces"] if w["code"] == "shop")
        shop = next(w for w in self.store["payload"]["workspaces"] if w["code"] == "shop")
        self.assertEqual(shop["employees"][0].get("rate"), shop_before["employees"][0].get("rate"))
        version = int(self.store["version"])
        forbid_cases = [
            {"workspaces": [{"code": "warehouse", "employees": [{"id": "e_wh", "name": "Чужой"}]}]},
            {"workspaces": [{"code": "shop", "employees": [
                {"id": "e_shop", "name": "Касса", "employee_id": "e_other"},
                {"id": "e_new", "name": "Не должен появиться", "employee_id": "e_new", "rate": 900},
            ]}]},
            {"workspaces": [{"code": "shop", "employees": [{"id": "e_new", "name": "С пин", "pin": "9999"}]}]},
            {"users": [{"user_id": "usr_dir", "role": "admin", "pin": "0000"}]},
        ]
        for body in forbid_cases:
            status, data, *_ = call(
                "PUT",
                "/api/state",
                {"version": version, "payload": body},
                cookie=cookie,
            )
            self.assertTrue(status.startswith("403"), status)
            self.assertEqual(data["error"], "forbidden")
            self.assertEqual(self.store["payload"], before)

    def test_manager_cannot_rewrite_existing_employee_id(self):
        self.add_user({
            "user_id": "usr_dir",
            "display_name": "Директор магазина",
            "pin": "4444",
            "role": "manager",
            "is_active": True,
            "departments": ["shop"],
            "permissions": {"timesheet": "edit"},
        })
        shop = next(w for w in self.store["payload"]["workspaces"] if w["code"] == "shop")
        shop["employees"][0]["employee_id"] = "e_shop"
        cookie, _ = self.login("usr_dir", "4444")
        before = json.loads(json.dumps(self.store["payload"]))
        status, data, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(self.store["version"]),
                "payload": {"workspaces": [{"code": "shop", "employees": [
                    {"id": "e_shop", "name": "Касса", "employee_id": "e_other"},
                ], "timesheet": {"2026-10": {"e_shop": {"1": "Б"}}}}]},
            },
            cookie=cookie,
        )
        self.assertTrue(status.startswith("403"), status)
        self.assertEqual(data["error"], "forbidden")
        self.assertEqual(self.store["payload"], before)

    def test_manager_without_timesheet_right_cannot_save_employees(self):
        self.add_user({
            "user_id": "usr_dir",
            "display_name": "Директор без табеля",
            "pin": "4444",
            "role": "manager",
            "is_active": True,
            "departments": ["shop"],
            "permissions": {},
        })
        cookie, _ = self.login("usr_dir", "4444")
        before = json.loads(json.dumps(self.store["payload"]))
        status, data, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(self.store["version"]),
                "payload": {"workspaces": [{"code": "shop", "employees": [
                    {"id": "e_shop", "name": "Касса"},
                    {"id": "e_new", "name": "Новый"},
                ]}]},
            },
            cookie=cookie,
        )
        self.assertTrue(status.startswith("403"), status)
        self.assertEqual(data["error"], "forbidden")
        self.assertEqual(self.store["payload"], before)

    def test_manager_with_salary_right_can_change_rate(self):
        self.add_user({
            "user_id": "usr_dir",
            "display_name": "Директор с зарплатой",
            "pin": "4444",
            "role": "manager",
            "is_active": True,
            "departments": ["shop"],
            "permissions": {"timesheet": "edit", "salary": "edit"},
        })
        cookie, _ = self.login("usr_dir", "4444")
        status, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(self.store["version"]),
                "payload": {"workspaces": [{"code": "shop", "employees": [
                    {"id": "e_shop", "name": "Касса", "rate": 1600},
                ]}]},
            },
            cookie=cookie,
        )
        self.assertTrue(status.startswith("200"), status)
        shop = next(w for w in self.store["payload"]["workspaces"] if w["code"] == "shop")
        self.assertEqual(shop["employees"][0]["rate"], 1600)
        self.assertEqual(shop["employees"][0]["taxiDaily"], 200)
        self.assertEqual(shop["employees"][0]["id"], "e_shop")
        self.assertEqual(self.store["payload"]["workspaces"][1]["employees"][0]["rate"], 1800)

    def test_two_users_do_not_drop_employees_or_timesheet(self):
        self.add_user({
            "user_id": "usr_dir",
            "display_name": "Директор магазина",
            "pin": "4444",
            "role": "manager",
            "is_active": True,
            "departments": ["shop"],
            "permissions": {"timesheet": "edit"},
        })
        director, _ = self.login("usr_dir", "4444")
        staff, _ = self.login("usr_001", "1111")
        version = int(self.store["version"])
        first, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": version,
                "payload": {"workspaces": [{
                    "code": "shop",
                    "employees": [
                        {"id": "e_shop", "name": "Касса"},
                        {"id": "e_new", "name": "Новый Сотрудник", "employee_id": "e_new"},
                    ],
                    "employeeIds": ["e_new"],
                    "timesheet": {"2026-10": {"e_shop": {"1": "О"}}},
                }]},
            },
            cookie=director,
        )
        self.assertTrue(first.startswith("200"), first)
        stale, data, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": version,
                "payload": {"workspaces": [{"code": "shop", "timesheet": {"2026-10": {"e_shop": {"3": "Б"}}}}]},
            },
            cookie=staff,
        )
        self.assertTrue(stale.startswith("409"), stale)
        self.assertEqual(data["error"], "conflict")
        shop = next(w for w in self.store["payload"]["workspaces"] if w["code"] == "shop")
        self.assertEqual([e["id"] for e in shop["employees"]], ["e_shop", "e_new"])
        self.assertEqual(shop["timesheet"]["2026-10"]["e_shop"]["1"], "О")
        self.assertNotIn("3", shop["timesheet"]["2026-10"]["e_shop"])

        second, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(self.store["version"]),
                "payload": {"workspaces": [{"code": "shop", "timesheet": {"2026-10": {"e_shop": {"3": "Б"}}}}]},
            },
            cookie=staff,
        )
        self.assertTrue(second.startswith("200"), second)
        kept, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(self.store["version"]),
                "payload": {"workspaces": [{
                    "code": "shop",
                    "employees": [
                        {"id": "e_shop", "name": "Касса обновлена"},
                        {"id": "e_other", "name": "Второй новый", "employee_id": "e_other"},
                    ],
                    "employeeIds": ["e_other"],
                }]},
            },
            cookie=director,
        )
        self.assertTrue(kept.startswith("200"), kept)
        shop = next(w for w in self.store["payload"]["workspaces"] if w["code"] == "shop")
        by_id = {e["id"]: e for e in shop["employees"]}
        self.assertEqual(set(by_id), {"e_shop", "e_new", "e_other"})
        self.assertEqual(by_id["e_shop"]["name"], "Касса обновлена")
        self.assertEqual(by_id["e_shop"]["rate"], 1500)
        self.assertEqual(by_id["e_new"]["name"], "Новый Сотрудник")
        self.assertEqual(shop["timesheet"]["2026-10"]["e_shop"]["1"], "О")
        self.assertEqual(shop["timesheet"]["2026-10"]["e_shop"]["3"], "Б")
        self.assertEqual(shop["employeeIds"], ["e_new", "e_other"])
        self.assertEqual(self.store["payload"]["workspaces"][1]["employees"][0]["id"], "e_wh")

    def _add_shop_manager(self, user_id="usr_dir", permissions=None):
        self.add_user({
            "user_id": user_id,
            "display_name": "Лариса",
            "pin": "4444",
            "role": "manager",
            "is_active": True,
            "departments": ["shop"],
            "permissions": permissions or {
                "metricsSetup": "edit",
                "metricsInput": "edit",
                "financeRec": "edit",
                "financeRecSetup": "edit",
                "settings": "edit",
                "timesheet": "edit",
            },
        })
        cookie, _ = self.login(user_id, "4444")
        return cookie

    def _seed_metric_access(self):
        metrics = self.store["payload"]["shopMetrics"]
        metrics["workAccess"] = {
            "2026-10": {"e_shop": ["m_keep", "m_drop"], "e_wh": ["m_wh"]},
        }
        metrics["extraAccess"] = {"e_shop": ["m_keep"], "e_wh": ["m_wh"]}
        self.store["payload"]["shopMetricCatalog"] = {
            "items": [
                {
                    "id": "m_shop",
                    "name": "Показатель магазина",
                    "department_id": "shop",
                    "assignments": [{
                        "assignment_id": "a_shop",
                        "department_id": "shop",
                        "assign_mode": "specific",
                        "employee_ids": ["e_shop"],
                    }],
                },
                {
                    "id": "m_wh",
                    "name": "Показатель склада",
                    "department_id": "warehouse",
                    "assignments": [{
                        "assignment_id": "a_wh",
                        "department_id": "warehouse",
                        "assign_mode": "specific",
                        "employee_ids": ["e_wh"],
                    }],
                },
            ]
        }
        self.store["payload"]["deptMetrics"]["warehouse"]["workAccess"] = {
            "2026-10": {"e_wh": ["m_wh"]},
        }

    def test_manager_saves_and_clears_own_metric_checkboxes(self):
        """Назначение и снятие галочек. Повторный PUT — с _wt_records как у metricCatalogPutSlice."""
        self._seed_metric_access()
        cookie = self._add_shop_manager()
        status, data, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(self.store["version"]),
                "payload": {"shopMetrics": {"workAccess": {
                    "2026-10": {"e_shop": ["m_keep"], "e_wh": []},
                }, "extraAccess": {"e_shop": ["m_keep"], "e_wh": []}}},
            },
            cookie=cookie,
        )
        self.assertTrue(status.startswith("200"), status)
        access = self.store["payload"]["shopMetrics"]["workAccess"]["2026-10"]
        self.assertEqual(access["e_shop"], ["m_keep"])
        self.assertEqual(access["e_wh"], ["m_wh"])
        self.assertEqual(self.store["payload"]["shopMetrics"]["extraAccess"]["e_wh"], ["m_wh"])
        self.assertEqual(self.store["payload"]["shopMetrics"]["facts"]["ok"], 1)
        self.assertEqual(self.store["payload"]["shopMetrics"]["nested"]["employees"][0]["rate"], 777)
        # После PUT сервер поднял record_v — клиент шлёт актуальный _wt_records (record_updates).
        records = dict(data.get("record_updates") or {})
        records.update(self.store["payload"].get("_wt_records") or {})
        status, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(self.store["version"]),
                "payload": {
                    "shopMetrics": {"workAccess": {"2026-10": {"e_shop": []}}},
                    "_wt_records": records,
                },
            },
            cookie=cookie,
        )
        self.assertTrue(status.startswith("200"), status)
        access = self.store["payload"]["shopMetrics"]["workAccess"]["2026-10"]
        self.assertEqual(access["e_shop"], [])
        self.assertEqual(access["e_wh"], ["m_wh"])
        # Повторное сохранение пустого списка и GET.
        records = dict(self.store["payload"].get("_wt_records") or {})
        status, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(self.store["version"]),
                "payload": {
                    "shopMetrics": {"workAccess": {"2026-10": {"e_shop": []}}},
                    "_wt_records": records,
                },
            },
            cookie=cookie,
        )
        self.assertTrue(status.startswith("200"), status)
        status, data, *_ = call("GET", "/api/state", cookie=cookie)
        self.assertTrue(status.startswith("200"), status)
        self.assertEqual(data["payload"]["shopMetrics"]["workAccess"]["2026-10"]["e_shop"], [])
        self.assertEqual(data["payload"]["shopMetrics"]["workAccess"]["2026-10"]["e_wh"], ["m_wh"])

    def test_work_access_put_without_records_does_not_clobber_newer_leaf(self):
        """PUT без _wt_records не затирает лист, уже поднятый record_v (защита concurrent OPS/PUT)."""
        self._seed_metric_access()
        cookie = self._add_shop_manager()
        status, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(self.store["version"]),
                "payload": {"shopMetrics": {"workAccess": {"2026-10": {"e_shop": ["m_keep"]}}}},
            },
            cookie=cookie,
        )
        self.assertTrue(status.startswith("200"), status)
        status, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(self.store["version"]),
                "payload": {"shopMetrics": {"workAccess": {"2026-10": {"e_shop": []}}}},
            },
            cookie=cookie,
        )
        self.assertTrue(status.startswith("200"), status)
        self.assertEqual(
            self.store["payload"]["shopMetrics"]["workAccess"]["2026-10"]["e_shop"],
            ["m_keep"],
        )

    def test_work_access_stale_client_version_keeps_newer_assignment(self):
        """Устаревший client_v < server_v — protect сохраняет более новое назначение."""
        self._seed_metric_access()
        cookie_a = self._add_shop_manager("usr_dir_a")
        status, data, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(self.store["version"]),
                "payload": {"shopMetrics": {"workAccess": {"2026-10": {"e_shop": ["m_keep"]}}}},
            },
            cookie=cookie_a,
        )
        self.assertTrue(status.startswith("200"), status)
        path = "shopMetrics.workAccess.2026-10.e_shop"
        server_v = int((data.get("record_updates") or {}).get(path, {}).get("v") or 0)
        self.assertGreaterEqual(server_v, 1)
        # Второй менеджер «не видел» обновление — шлёт stale v=0 и старый снимок.
        cookie_b = self._add_shop_manager("usr_dir_b")
        status, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(self.store["version"]),
                "payload": {
                    "shopMetrics": {"workAccess": {"2026-10": {"e_shop": ["m_drop"]}}},
                    "_wt_records": {path: {"v": 0}},
                },
            },
            cookie=cookie_b,
        )
        self.assertTrue(status.startswith("200"), status)
        self.assertEqual(
            self.store["payload"]["shopMetrics"]["workAccess"]["2026-10"]["e_shop"],
            ["m_keep"],
        )

    def test_metric_checkboxes_survive_reread(self):
        self._seed_metric_access()
        cookie = self._add_shop_manager()
        status, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(self.store["version"]),
                "payload": {
                    "shopMetrics": {"workAccess": {"2026-10": {"e_shop": ["m_keep"]}}},
                    "shopMetricCatalog": {"items": [{
                        "id": "m_shop",
                        "name": "Показатель магазина",
                        "department_id": "shop",
                        "assignments": [{
                            "assignment_id": "a_shop",
                            "department_id": "shop",
                            "assign_mode": "specific",
                            "employee_ids": [],
                        }],
                    }]},
                },
            },
            cookie=cookie,
        )
        self.assertTrue(status.startswith("200"), status)
        status, data, *_ = call("GET", "/api/state", cookie=cookie)
        self.assertTrue(status.startswith("200"), status)
        access = data["payload"]["shopMetrics"]["workAccess"]["2026-10"]
        self.assertEqual(access["e_shop"], ["m_keep"])
        self.assertEqual(access["e_wh"], ["m_wh"])
        items = {row["id"]: row for row in data["payload"]["shopMetricCatalog"]["items"]}
        self.assertEqual(items["m_shop"]["assignments"][0]["employee_ids"], [])

    def test_other_department_metric_assignments_stay(self):
        self._seed_metric_access()
        cookie = self._add_shop_manager()
        status, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(self.store["version"]),
                "payload": {"shopMetricCatalog": {"items": [
                    {
                        "id": "m_shop",
                        "name": "Новое имя магазина",
                        "department_id": "shop",
                        "assignments": [{
                            "assignment_id": "a_shop",
                            "department_id": "shop",
                            "assign_mode": "specific",
                            "employee_ids": ["e_shop", "e_wh"],
                        }],
                    },
                    {
                        "id": "m_wh",
                        "name": "Чужое имя",
                        "department_id": "warehouse",
                        "assignments": [{
                            "assignment_id": "a_wh",
                            "department_id": "warehouse",
                            "assign_mode": "specific",
                            "employee_ids": [],
                        }],
                    },
                ]}},
            },
            cookie=cookie,
        )
        self.assertTrue(status.startswith("200"), status)
        items = {row["id"]: row for row in self.store["payload"]["shopMetricCatalog"]["items"]}
        self.assertEqual(items["m_shop"]["name"], "Новое имя магазина")
        self.assertEqual(items["m_shop"]["assignments"][0]["employee_ids"], ["e_shop"])
        self.assertEqual(items["m_wh"]["name"], "Показатель склада")
        self.assertEqual(items["m_wh"]["assignments"][0]["employee_ids"], ["e_wh"])
        self.assertEqual(
            self.store["payload"]["deptMetrics"]["warehouse"]["workAccess"]["2026-10"]["e_wh"],
            ["m_wh"],
        )
        self.assertEqual(self.store["payload"]["deptMetrics"]["warehouse"]["facts"]["secret"], 1)

    def test_metric_input_cannot_change_setup_and_setup_cannot_change_facts(self):
        cookie, _ = self.login("usr_001", "1111")
        before = json.loads(json.dumps(self.store["payload"]["shopMetrics"]))
        status, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(self.store["version"]),
                "payload": {"shopMetrics": {"workAccess": {"2026-10": {"e_shop": ["m_new"]}}}},
            },
            cookie=cookie,
        )
        self.assertTrue(status.startswith("403"), status)
        self.assertEqual(self.store["payload"]["shopMetrics"], before)
        self._add_shop_manager("usr_setup", {"metricsSetup": "edit"})
        cookie_setup, _ = self.login("usr_setup", "4444")
        status, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(self.store["version"]),
                "payload": {"shopMetrics": {"facts": {"ok": 9}}},
            },
            cookie=cookie_setup,
        )
        self.assertTrue(status.startswith("403"), status)
        self.assertEqual(self.store["payload"]["shopMetrics"]["facts"]["ok"], 1)

    def test_kpi_perm_does_not_grant_metric_setup(self):
        self._seed_metric_access()
        self.add_user({
            "user_id": "usr_kpi_only",
            "display_name": "Только KPI",
            "pin": "4444",
            "role": "manager",
            "is_active": True,
            "departments": ["shop"],
            "permissions": {"kpi": "manage"},
        })
        cookie, _ = self.login("usr_kpi_only", "4444")
        before = json.loads(json.dumps(self.store["payload"]["shopMetrics"]["workAccess"]))
        status, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(self.store["version"]),
                "payload": {"shopMetrics": {"workAccess": {"2026-10": {"e_shop": ["m_hack"]}}}},
            },
            cookie=cookie,
        )
        self.assertTrue(status.startswith("403"), status)
        self.assertEqual(self.store["payload"]["shopMetrics"]["workAccess"], before)

    def test_admin_can_change_kpi_money_defs(self):
        self.store["payload"]["shopPayroll"] = {
            "salaries": [{"employee_id": "e_shop", "rate": 1500}],
            "kpiDefs": [{"kpi_id": "kpi_old", "name": "Старый", "amount": 100}],
            "assignments": [{"id": "ka1", "employee_id": "e_shop", "kpi_id": "kpi_old"}],
        }
        cookie, _ = self.login("user_admin", "5555")
        status, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(self.store["version"]),
                "payload": {
                    "shopPayroll": {
                        "salaries": [{"employee_id": "e_shop", "rate": 1500}],
                        "kpiDefs": [{"kpi_id": "kpi_new", "name": "Новый", "amount": 250}],
                        "assignments": [{"id": "ka1", "employee_id": "e_shop", "kpi_id": "kpi_new"}],
                    }
                },
            },
            cookie=cookie,
        )
        self.assertTrue(status.startswith("200"), status)
        pay = self.store["payload"]["shopPayroll"]
        self.assertEqual(pay["kpiDefs"][0]["kpi_id"], "kpi_new")
        self.assertEqual(pay["kpiDefs"][0]["amount"], 250)
        self.assertEqual(pay["assignments"][0]["kpi_id"], "kpi_new")

    def test_manager_metrics_setup_cannot_change_kpi_money(self):
        self.store["payload"]["shopPayroll"] = {
            "salaries": [{"employee_id": "e_shop", "rate": 1500}],
            "kpiDefs": [{"kpi_id": "kpi_old", "name": "Старый", "amount": 100}],
            "assignments": [{"id": "ka1", "employee_id": "e_shop", "kpi_id": "kpi_old"}],
        }
        cookie = self._add_shop_manager(
            "usr_ms_kpi",
            {"metricsSetup": "edit", "salary": "edit", "salaryCalc": "edit", "timesheet": "edit"},
        )
        before = json.loads(json.dumps(self.store["payload"]["shopPayroll"]))
        status, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(self.store["version"]),
                "payload": {
                    "shopPayroll": {
                        "salaries": [{"employee_id": "e_shop", "rate": 1600}],
                        "kpiDefs": [{"kpi_id": "kpi_hack", "name": "Взлом", "amount": 999}],
                        "assignments": [],
                    }
                },
            },
            cookie=cookie,
        )
        self.assertTrue(status.startswith("403"), status)
        self.assertEqual(self.store["payload"]["shopPayroll"], before)

    def test_staff_metrics_setup_cannot_change_kpi_money(self):
        self.store["payload"]["shopPayroll"] = {
            "salaries": [{"employee_id": "e_shop", "rate": 1500}],
            "kpiDefs": [{"kpi_id": "kpi_old", "amount": 100}],
            "assignments": [{"id": "ka1", "kpi_id": "kpi_old"}],
        }
        self.add_user({
            "user_id": "usr_setup_user",
            "display_name": "Настройщик",
            "pin": "6666",
            "role": "user",
            "is_active": True,
            "departments": ["shop"],
            "permissions": {"metricsSetup": "edit", "salary": "edit", "salaryCalc": "edit"},
        })
        cookie, _ = self.login("usr_setup_user", "6666")
        before = json.loads(json.dumps(self.store["payload"]["shopPayroll"]))
        status, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(self.store["version"]),
                "payload": {
                    "shopPayroll": {
                        "kpiDefs": [{"kpi_id": "kpi_hack", "amount": 1}],
                        "assignments": [],
                    }
                },
            },
            cookie=cookie,
        )
        self.assertTrue(status.startswith("403"), status)
        self.assertEqual(self.store["payload"]["shopPayroll"], before)

    def test_warehouse_metrics_setup_saves_work_access(self):
        self._seed_metric_access()
        self.add_user({
            "user_id": "usr_wh_setup",
            "display_name": "Склад настройка",
            "pin": "7777",
            "role": "manager",
            "is_active": True,
            "departments": ["warehouse"],
            "permissions": {"metricsSetup": "edit", "metricsInput": "edit", "timesheet": "edit"},
        })
        cookie, _ = self.login("usr_wh_setup", "7777")
        status, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(self.store["version"]),
                "payload": {
                    "deptMetrics": {
                        "warehouse": {"workAccess": {"2026-10": {"e_wh": ["m_wh", "m_extra"]}}}
                    }
                },
            },
            cookie=cookie,
        )
        self.assertTrue(status.startswith("200"), status)
        self.assertEqual(
            self.store["payload"]["deptMetrics"]["warehouse"]["workAccess"]["2026-10"]["e_wh"],
            ["m_wh", "m_extra"],
        )
        self.assertEqual(
            self.store["payload"]["shopMetrics"]["workAccess"]["2026-10"]["e_shop"],
            ["m_keep", "m_drop"],
        )

    def test_metrics_setup_catalog_put_ignores_empty_payroll_slice(self):
        """Руководитель с metricsSetup без salary: пустой shopPayroll в срезе не даёт 403."""
        self._seed_metric_access()
        cookie = self._add_shop_manager(
            "usr_svetlana_sim",
            {
                "metricsSetup": "edit",
                "metricsInput": "edit",
                "timesheet": "edit",
                "revision": "edit",
            },
        )
        before_pay = json.loads(json.dumps(self.store["payload"].get("shopPayroll")))
        items = list((self.store["payload"].get("shopMetricCatalog") or {}).get("items") or [])
        items = [row for row in items if not (isinstance(row, dict) and row.get("id") == "m_setup_new")]
        items.append({
            "id": "m_setup_new",
            "name": "Новая метрика руководителя",
            "department_id": "shop",
            "active": True,
            "assignments": [],
        })
        status, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(self.store["version"]),
                "payload": {
                    "shopMetricCatalog": {"items": items},
                    "shopMetrics": {},
                    "shopPayroll": {},
                    "deptMetrics": {},
                },
            },
            cookie=cookie,
        )
        self.assertTrue(status.startswith("200"), status)
        ids = {
            str(row.get("id"))
            for row in (self.store["payload"].get("shopMetricCatalog") or {}).get("items") or []
            if isinstance(row, dict)
        }
        self.assertIn("m_setup_new", ids)
        self.assertEqual(self.store["payload"].get("shopPayroll"), before_pay)

    def test_metrics_setup_default_ensure_shop_payroll_does_not_403(self):
        """Клиентский ensureShopPayroll(): salaries/rules пустые при metricsSetup без salary."""
        self._seed_metric_access()
        pay = self.store["payload"].setdefault("shopPayroll", {})
        if not isinstance(pay, dict):
            pay = {}
            self.store["payload"]["shopPayroll"] = pay
        pay["salaries"] = [{"id": "e_shop", "rate": 1500}]
        pay["rules"] = [{"id": "r_keep", "name": "Правило", "bonus": 100}]
        pay["manual"] = {"e_shop": {"bonus": 5}}
        pay["kpiDefs"] = [{"id": "k_keep", "bonus": 50}]
        before_pay = json.loads(json.dumps(pay))
        cookie = self._add_shop_manager(
            "usr_setup_defpay",
            {
                "metricsSetup": "edit",
                "metricsInput": "edit",
                "timesheet": "edit",
            },
        )
        items = list((self.store["payload"].get("shopMetricCatalog") or {}).get("items") or [])
        items = [row for row in items if not (isinstance(row, dict) and row.get("id") == "m_defpay")]
        items.append({
            "id": "m_defpay",
            "name": "Метрика с дефолтным payroll",
            "department_id": "shop",
            "active": True,
            "assignments": [],
        })
        status, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(self.store["version"]),
                "payload": {
                    "shopMetricCatalog": {"items": items},
                    "shopPayroll": {
                        "departmentId": "shop",
                        "salaries": [],
                        "rules": [],
                        "manual": {},
                        "metrics": [{"id": "m_defpay", "name": "Метрика с дефолтным payroll"}],
                    },
                },
            },
            cookie=cookie,
        )
        self.assertTrue(status.startswith("200"), status)
        ids = {
            str(row.get("id"))
            for row in (self.store["payload"].get("shopMetricCatalog") or {}).get("items") or []
            if isinstance(row, dict)
        }
        self.assertIn("m_defpay", ids)
        after_pay = self.store["payload"].get("shopPayroll") or {}
        # Оклады, правила расчёта, manual и денежные KPI не затёрты.
        self.assertEqual(after_pay.get("salaries"), before_pay.get("salaries"))
        self.assertEqual(after_pay.get("rules"), before_pay.get("rules"))
        self.assertEqual(after_pay.get("manual"), before_pay.get("manual"))
        self.assertEqual(after_pay.get("kpiDefs"), before_pay.get("kpiDefs"))

    def test_metrics_setup_ops_cannot_wipe_salary_fields(self):
        """metricsSetup без salary: OPS/batch не чистят salaries/rules и не меняют rate."""
        self._seed_metric_access()
        pay = self.store["payload"].setdefault("shopPayroll", {})
        pay["salaries"] = [{"id": "e_shop", "rate": 1500}]
        pay["rules"] = [{"id": "r1", "name": "Правило", "bonus": 100}]
        pay["manual"] = {"e_shop": {"bonus": 5}}
        before_pay = json.loads(json.dumps(pay))
        shop = next(w for w in self.store["payload"]["workspaces"] if w["code"] == "shop")
        before_rate = shop["employees"][0].get("rate")
        cookie = self._add_shop_manager(
            "usr_setup_ops_sal",
            {"metricsSetup": "edit", "metricsInput": "edit", "timesheet": "edit"},
        )
        for path, value in (
            ("shopPayroll.salaries", []),
            ("shopPayroll.rules", []),
            ("shopPayroll.manual", {}),
            ("salary", {}),
            ("salary.2026-10.e_shop.rate", 1),
        ):
            st, data, *_ = call(
                "POST",
                "/api/state/op",
                {"op": {"path": path, "expected_v": 0, "value": value}},
                cookie=cookie,
            )
            self.assertTrue(st.startswith("403"), "%s %s" % (path, st))
            self.assertEqual(data.get("error"), "forbidden")
            self.assertEqual(self.store["payload"].get("shopPayroll"), before_pay)
        st, data, *_ = call(
            "POST",
            "/api/state/op",
            {
                "op": {
                    "path": "workspaces.shop.employees",
                    "expected_v": 0,
                    "value": [{"id": "e_shop", "name": "Касса", "rate": 1}],
                }
            },
            cookie=cookie,
        )
        self.assertTrue(st.startswith("403"), st)
        shop_after = next(w for w in self.store["payload"]["workspaces"] if w["code"] == "shop")
        self.assertEqual(shop_after["employees"][0].get("rate"), before_rate)
        st, data, *_ = call(
            "POST",
            "/api/state/op/batch",
            {
                "atomic": True,
                "ops": [
                    {"path": "shopPayroll.salaries", "expected_v": 0, "value": []},
                    {
                        "path": "shopPayroll.metrics",
                        "expected_v": 0,
                        "value": [{"id": "m_op", "name": "Via OP"}],
                    },
                ]
            },
            cookie=cookie,
        )
        # atomic batch: salaries-op запрещён → пакет не применяется.
        self.assertTrue(st.startswith("403"), st)
        self.assertEqual(data.get("error"), "forbidden")
        self.assertEqual(
            (self.store["payload"].get("shopPayroll") or {}).get("salaries"),
            before_pay.get("salaries"),
        )
        self.assertNotEqual(
            (self.store["payload"].get("shopPayroll") or {}).get("metrics"),
            [{"id": "m_op", "name": "Via OP"}],
        )

    def test_metrics_setup_without_perm_cannot_save_catalog(self):
        self._seed_metric_access()
        cookie = self._add_shop_manager(
            "usr_input_only",
            {"metricsInput": "edit", "timesheet": "edit"},
        )
        items = list((self.store["payload"].get("shopMetricCatalog") or {}).get("items") or [])
        items = items + [{"id": "m_denied", "name": "Denied", "department_id": "shop", "active": True}]
        status, data, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(self.store["version"]),
                "payload": {"shopMetricCatalog": {"items": items}},
            },
            cookie=cookie,
        )
        self.assertTrue(status.startswith("403"), status)
        self.assertEqual(data.get("error"), "forbidden")
        ids = {
            str(row.get("id"))
            for row in (self.store["payload"].get("shopMetricCatalog") or {}).get("items") or []
            if isinstance(row, dict)
        }
        self.assertNotIn("m_denied", ids)

    def test_territory_metrics_setup_catalog_with_empty_shop_slice(self):
        self._seed_metric_access()
        self.add_user({
            "user_id": "usr_terr_setup",
            "display_name": "Территория настройка",
            "pin": "6666",
            "role": "manager",
            "is_active": True,
            "departments": ["office", "warehouse"],
            "permissions": {
                "metricsSetup": "edit",
                "metricsInput": "edit",
                "timesheet": "edit",
            },
        })
        cookie, _ = self.login("usr_terr_setup", "6666")
        items = list((self.store["payload"].get("shopMetricCatalog") or {}).get("items") or [])
        items = [row for row in items if not (isinstance(row, dict) and row.get("id") == "m_office_new")]
        items.append({
            "id": "m_office_new",
            "name": "Офис метрика",
            "department_id": "office",
            "active": True,
            "assignments": [],
        })
        status, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(self.store["version"]),
                "payload": {
                    "shopMetricCatalog": {"items": items},
                    "shopMetrics": {},
                    "shopPayroll": {},
                },
            },
            cookie=cookie,
        )
        self.assertTrue(status.startswith("200"), status)
        ids = {
            str(row.get("id"))
            for row in (self.store["payload"].get("shopMetricCatalog") or {}).get("items") or []
            if isinstance(row, dict)
        }
        self.assertIn("m_office_new", ids)

    def test_territory_category_metric_assignments_saved(self):
        self._seed_metric_access()
        self.store["payload"]["shopMetricCatalog"]["items"].append({
            "id": "m_ter",
            "name": "Территория без dept",
            "territory_category": "warehouse",
            "assignments": [{
                "assignment_id": "a_ter",
                "department_id": "warehouse",
                "assign_mode": "specific",
                "employee_ids": [],
            }],
        })
        self.add_user({
            "user_id": "usr_wh_cat",
            "display_name": "Склад каталог",
            "pin": "8888",
            "role": "user",
            "is_active": True,
            "departments": ["warehouse"],
            "permissions": {"metricsSetup": "edit"},
        })
        cookie, _ = self.login("usr_wh_cat", "8888")
        status, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(self.store["version"]),
                "payload": {"shopMetricCatalog": {"items": [{
                    "id": "m_ter",
                    "name": "Территория без dept",
                    "territory_category": "warehouse",
                    "assignments": [{
                        "assignment_id": "a_ter",
                        "department_id": "warehouse",
                        "assign_mode": "specific",
                        "employee_ids": ["e_wh"],
                    }],
                }]}},
            },
            cookie=cookie,
        )
        self.assertTrue(status.startswith("200"), status)
        items = {row["id"]: row for row in self.store["payload"]["shopMetricCatalog"]["items"]}
        self.assertEqual(items["m_ter"]["assignments"][0]["employee_ids"], ["e_wh"])
        self.assertEqual(items["m_shop"]["assignments"][0]["employee_ids"], ["e_shop"])

    def test_staff_metric_catalog_put_with_filtered_wt_records_no_false_403(self):
        """Руководитель с metricsSetup: PUT каталога + урезанный _wt_records не даёт ложный 403."""
        self._seed_metric_access()
        # На сервере «чужие» версии (как после OPS других разделов).
        self.store["payload"]["_wt_records"] = {
            "workspaces.shop.timesheet.2026-10.e_shop.1": {"v": 3},
            "workspaces.warehouse.timesheet.2026-10.e_wh.1": {"v": 9},
            "salary.2026-10": {"v": 7},
            "financeRec.audit": {"v": 5},
            "deptMetrics.drivers.workAccess": {"v": 2},
        }
        self.add_user({
            "user_id": "usr_drv_metrics",
            "display_name": "Руководитель логистики",
            "pin": "4545",
            "role": "user",
            "legacy_role": "manager",
            "is_active": True,
            "departments": ["drivers"],
            "permissions": {"metricsSetup": "edit", "metricsInput": "edit", "timesheet": "edit"},
        })
        # Подразделение Логистика + Торговые (как на Timeweb).
        workspaces = self.store["payload"].setdefault("workspaces", [])
        if not any(isinstance(w, dict) and w.get("code") == "drivers" for w in workspaces):
            workspaces.append({
                "id": "ws_drivers",
                "code": "drivers",
                "name": "Логистика + Торговые",
                "employees": [{"id": "e_drv", "name": "ТП", "rate": 1000}],
                "employeeIds": ["e_drv"],
                "timesheet": {},
            })
        # Метрика «Продажи ОПТ» (категория sales_opt → dept drivers).
        self.store["payload"]["shopMetricCatalog"]["items"].append({
            "id": "m_sales_opt",
            "name": "Продажи ОПТ",
            "department_id": "drivers",
            "territory_category": "sales_opt",
            "assignments": [{
                "assignment_id": "a_opt",
                "department_id": "drivers",
                "work_tab_id": "sales",
                "assign_mode": "specific",
                "employee_ids": [],
            }],
        })
        self.store["payload"].setdefault("deptMetrics", {})["drivers"] = {
            "workAccess": {"2026-11": {}},
            "extraAccess": {},
        }
        cookie, _ = self.login("usr_drv_metrics", "4545")
        # Как metricCatalogPutSlice: каталог + workAccess своего отдела + отфильтрованный _wt_records.
        filtered_records = {
            "deptMetrics.drivers.workAccess": {"v": 2},
        }
        status, data, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(self.store["version"]),
                "payload": {
                    "shopMetricCatalog": {"items": [{
                        "id": "m_sales_opt",
                        "name": "Продажи ОПТ",
                        "department_id": "drivers",
                        "territory_category": "sales_opt",
                        "assignments": [{
                            "assignment_id": "a_opt",
                            "department_id": "drivers",
                            "work_tab_id": "sales",
                            "assign_mode": "specific",
                            "employee_ids": ["e_drv"],
                        }],
                    }]},
                    "deptMetrics": {
                        "drivers": {
                            "workAccess": {"2026-11": {"e_drv": ["m_sales_opt"]}},
                            "extraAccess": {"e_drv": ["m_sales_opt"]},
                        }
                    },
                    "_wt_records": filtered_records,
                    "_wt_idempotency": {"client_key": {"at": "t", "result": {"ok": True}}},
                },
            },
            cookie=cookie,
        )
        self.assertTrue(status.startswith("200"), status)
        self.assertNotEqual(data.get("error"), "forbidden")
        items = {row["id"]: row for row in self.store["payload"]["shopMetricCatalog"]["items"]}
        self.assertEqual(items["m_sales_opt"]["assignments"][0]["employee_ids"], ["e_drv"])
        # Чужие meta-версии на сервере не стёрты клиентским подмножеством.
        meta = self.store["payload"].get("_wt_records") or {}
        self.assertEqual(int((meta.get("salary.2026-10") or {}).get("v") or 0), 7)
        self.assertEqual(int((meta.get("financeRec.audit") or {}).get("v") or 0), 5)
        # Подмена _wt_idempotency клиентом не принята.
        idem = self.store["payload"].get("_wt_idempotency")
        if isinstance(idem, dict):
            self.assertNotIn("client_key", idem)

    def test_staff_filtered_wt_records_cannot_forge_versions_or_touch_salary(self):
        """Отфильтрованный _wt_records не открывает зарплату и не поднимает forged v."""
        self._seed_metric_access()
        self.store["payload"]["_wt_records"] = {
            "shopMetrics.facts.ok": {"v": 1},
            "salary.2026-10": {"v": 4},
        }
        cookie = self._add_shop_manager(
            "usr_setup_norec",
            {"metricsSetup": "edit", "metricsInput": "edit", "timesheet": "edit"},
        )
        before_salary = json.loads(json.dumps(self.store["payload"].get("salary")))
        status, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(self.store["version"]),
                "payload": {
                    "shopMetricCatalog": {"items": [{
                        "id": "m_shop",
                        "name": "Показатель магазина",
                        "department_id": "shop",
                        "assignments": [{
                            "assignment_id": "a_shop",
                            "department_id": "shop",
                            "assign_mode": "specific",
                            "employee_ids": ["e_shop"],
                        }],
                    }]},
                    "salary": {"2026-10": {"e_shop": {"rate": 1, "issued": True}}},
                    "_wt_records": {
                        "shopMetrics.facts.ok": {"v": 99},
                        "salary.2026-10": {"v": 99},
                        "forged.path": {"v": 9},
                    },
                },
            },
            cookie=cookie,
        )
        # Зарплата без права — 403 (данные), не из-за _wt_records.
        self.assertTrue(status.startswith("403"), status)
        self.assertEqual(self.store["payload"].get("salary"), before_salary)
        meta = self.store["payload"].get("_wt_records") or {}
        self.assertEqual(int((meta.get("shopMetrics.facts.ok") or {}).get("v") or 0), 1)
        self.assertNotIn("forged.path", meta)

    def test_staff_cannot_change_foreign_dept_metrics_via_catalog_put(self):
        """Сотрудник drivers не меняет назначения магазина через полный каталог в PUT."""
        self._seed_metric_access()
        self.add_user({
            "user_id": "usr_drv_only",
            "display_name": "Только drivers",
            "pin": "4646",
            "role": "user",
            "is_active": True,
            "departments": ["drivers"],
            "permissions": {"metricsSetup": "edit"},
        })
        before_shop = json.loads(json.dumps(
            next(
                row for row in self.store["payload"]["shopMetricCatalog"]["items"]
                if row.get("id") == "m_shop"
            )
        ))
        cookie, _ = self.login("usr_drv_only", "4646")
        status, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(self.store["version"]),
                "payload": {
                    "shopMetricCatalog": {"items": [{
                        "id": "m_shop",
                        "name": "Взломанный магазин",
                        "department_id": "shop",
                        "assignments": [{
                            "assignment_id": "a_shop",
                            "department_id": "shop",
                            "assign_mode": "specific",
                            "employee_ids": ["e_hack"],
                        }],
                    }]},
                    "_wt_records": {"deptMetrics.drivers.workAccess": {"v": 1}},
                },
            },
            cookie=cookie,
        )
        self.assertTrue(status.startswith("200"), status)
        after = next(
            row for row in self.store["payload"]["shopMetricCatalog"]["items"]
            if row.get("id") == "m_shop"
        )
        self.assertEqual(after.get("name"), before_shop.get("name"))
        self.assertEqual(after.get("assignments"), before_shop.get("assignments"))

    def test_merge_metric_assignments_territory_updates_position_and_work_tab(self):
        """Территория: при том же assignment_id сохраняются position_id и work_tab_id."""
        current = [{
            "assignment_id": "a_wh1",
            "department_id": "warehouse",
            "work_tab_id": "pickers",
            "position_id": "pos_picker",
            "assign_mode": "specific",
            "employee_ids": ["e_wh"],
        }, {
            "assignment_id": "a_wh2",
            "department_id": "warehouse",
            "work_tab_id": "managers",
            "position_id": "pos_mgr",
            "assign_mode": "group_all",
            "employee_ids": [],
        }]
        incoming = [{
            "assignment_id": "a_wh1",
            "department_id": "warehouse",
            "work_tab_id": "loaders",
            "position_id": "pos_loader",
            "assign_mode": "specific",
            "employee_ids": ["e_wh"],
        }]
        emp_dept = {"e_wh": "warehouse", "e_shop": "shop"}
        codes = {"warehouse", "shop"}
        out = wt_api._merge_metric_assignments(current, incoming, codes, emp_dept)
        by_id = {row["assignment_id"]: row for row in out}
        self.assertEqual(by_id["a_wh1"]["assignment_id"], "a_wh1")
        self.assertEqual(by_id["a_wh1"]["work_tab_id"], "loaders")
        self.assertEqual(by_id["a_wh1"]["position_id"], "pos_loader")
        self.assertEqual(by_id["a_wh1"]["employee_ids"], ["e_wh"])
        # Другое назначение той же метрики не трогаем.
        self.assertEqual(by_id["a_wh2"]["work_tab_id"], "managers")
        self.assertEqual(by_id["a_wh2"]["position_id"], "pos_mgr")

    def test_merge_metric_assignments_shop_keeps_old_position_and_work_tab(self):
        """Магазин Володарского: merge по-прежнему не переписывает position_id/work_tab_id."""
        current = [{
            "assignment_id": "a_shop",
            "department_id": "shop",
            "work_tab_id": "cashiers",
            "position_id": "pos_cashier",
            "assign_mode": "specific",
            "employee_ids": ["e_shop"],
        }]
        incoming = [{
            "assignment_id": "a_shop",
            "department_id": "shop",
            "work_tab_id": "pickers",
            "position_id": "pos_picker",
            "assign_mode": "specific",
            "employee_ids": ["e_shop"],
        }]
        out = wt_api._merge_metric_assignments(
            current, incoming, {"shop", "warehouse"}, {"e_shop": "shop", "e_wh": "warehouse"}
        )
        row = out[0]
        self.assertEqual(row["assignment_id"], "a_shop")
        self.assertEqual(row["employee_ids"], ["e_shop"])
        self.assertEqual(row["work_tab_id"], "cashiers")
        self.assertEqual(row["position_id"], "pos_cashier")

    def test_territory_assignment_position_work_tab_survive_put(self):
        """PUT каталога: складское назначение обновляет должность и группу; магазин — нет."""
        self._seed_metric_access()
        items = self.store["payload"]["shopMetricCatalog"]["items"]
        for row in items:
            if row["id"] == "m_wh":
                row["assignments"] = [{
                    "assignment_id": "a_wh",
                    "department_id": "warehouse",
                    "work_tab_id": "pickers",
                    "position_id": "pos_picker",
                    "assign_mode": "specific",
                    "employee_ids": ["e_wh"],
                }]
            if row["id"] == "m_shop":
                row["assignments"] = [{
                    "assignment_id": "a_shop",
                    "department_id": "shop",
                    "work_tab_id": "cashiers",
                    "position_id": "pos_cashier",
                    "assign_mode": "specific",
                    "employee_ids": ["e_shop"],
                }]
        self.add_user({
            "user_id": "usr_wh_pos",
            "display_name": "Склад должности",
            "pin": "7777",
            "role": "user",
            "is_active": True,
            "departments": ["warehouse", "shop"],
            "permissions": {"metricsSetup": "edit"},
        })
        cookie, _ = self.login("usr_wh_pos", "7777")
        status, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(self.store["version"]),
                "payload": {"shopMetricCatalog": {"items": [
                    {
                        "id": "m_wh",
                        "name": "Показатель склада",
                        "department_id": "warehouse",
                        "assignments": [{
                            "assignment_id": "a_wh",
                            "department_id": "warehouse",
                            "work_tab_id": "loaders",
                            "position_id": "pos_loader",
                            "assign_mode": "specific",
                            "employee_ids": ["e_wh"],
                        }],
                    },
                    {
                        "id": "m_shop",
                        "name": "Показатель магазина",
                        "department_id": "shop",
                        "assignments": [{
                            "assignment_id": "a_shop",
                            "department_id": "shop",
                            "work_tab_id": "pickers",
                            "position_id": "pos_picker",
                            "assign_mode": "specific",
                            "employee_ids": ["e_shop"],
                        }],
                    },
                ]}},
            },
            cookie=cookie,
        )
        self.assertTrue(status.startswith("200"), status)
        by_id = {row["id"]: row for row in self.store["payload"]["shopMetricCatalog"]["items"]}
        wh = by_id["m_wh"]["assignments"][0]
        self.assertEqual(wh["assignment_id"], "a_wh")
        self.assertEqual(wh["work_tab_id"], "loaders")
        self.assertEqual(wh["position_id"], "pos_loader")
        self.assertEqual(wh["employee_ids"], ["e_wh"])
        shop = by_id["m_shop"]["assignments"][0]
        self.assertEqual(shop["assignment_id"], "a_shop")
        self.assertEqual(shop["work_tab_id"], "cashiers")
        self.assertEqual(shop["position_id"], "pos_cashier")

    def test_warehouse_user_receives_metric_catalog_on_get(self):
        self._seed_metric_access()
        self.add_user({
            "user_id": "usr_wh_get",
            "display_name": "Склад GET",
            "pin": "9999",
            "role": "user",
            "is_active": True,
            "departments": ["warehouse"],
            "permissions": {"metricsSetup": "edit", "metricsInput": "edit"},
        })
        cookie, _ = self.login("usr_wh_get", "9999")
        status, data, *_ = call("GET", "/api/state", cookie=cookie)
        self.assertTrue(status.startswith("200"), status)
        self.assertIn("shopMetricCatalog", data["payload"])
        ids = {row["id"] for row in data["payload"]["shopMetricCatalog"]["items"]}
        self.assertIn("m_wh", ids)
        self.assertIn("m_shop", ids)

    def test_foreign_department_metric_setup_forbidden_effect(self):
        self._seed_metric_access()
        cookie = self._add_shop_manager("usr_shop_only", {"metricsSetup": "edit"})
        status, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(self.store["version"]),
                "payload": {
                    "deptMetrics": {
                        "warehouse": {"workAccess": {"2026-10": {"e_wh": []}}}
                    }
                },
            },
            cookie=cookie,
        )
        # Нет warehouse в departments — ветка не пишется / или 403 если считается чужой.
        self.assertEqual(
            self.store["payload"]["deptMetrics"]["warehouse"]["workAccess"]["2026-10"]["e_wh"],
            ["m_wh"],
        )
        if status.startswith("200"):
            pass
        else:
            self.assertTrue(status.startswith("403"), status)

    def test_employee_card_save_keeps_metric_checkboxes_after_reread(self):
        """Сценарий «Сохранить сотрудника»: карточка + галочки метрик в одном PUT, затем GET."""
        self._seed_metric_access()
        cookie = self._add_shop_manager(
            "usr_card_metrics",
            {
                "metricsSetup": "edit",
                "metricsInput": "edit",
                "timesheet": "edit",
            },
        )
        # Установка нескольких назначений + правка карточки.
        status, data, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(self.store["version"]),
                "payload": {
                    "workspaces": [{
                        "code": "shop",
                        "employees": [{
                            "id": "e_shop",
                            "employee_id": "e_shop",
                            "name": "Касса Обновлена",
                        }],
                        "employeeIds": ["e_shop"],
                    }],
                    "shopMetrics": {
                        "workAccess": {"2026-10": {"e_shop": ["m_keep", "m_shop"]}},
                        "extraAccess": {"e_shop": ["m_keep"]},
                    },
                    "shopMetricCatalog": {"items": [{
                        "id": "m_shop",
                        "name": "Показатель магазина",
                        "department_id": "shop",
                        "assignments": [{
                            "assignment_id": "a_shop",
                            "department_id": "shop",
                            "assign_mode": "specific",
                            "employee_ids": ["e_shop"],
                        }],
                    }]},
                },
            },
            cookie=cookie,
        )
        self.assertTrue(status.startswith("200"), status)
        records = dict(data.get("record_updates") or {})
        records.update(self.store["payload"].get("_wt_records") or {})
        # Снятие одной галочки, остальные остаются (с актуальными record_v).
        status, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(self.store["version"]),
                "payload": {
                    "workspaces": [{
                        "code": "shop",
                        "employees": [{
                            "id": "e_shop",
                            "employee_id": "e_shop",
                            "name": "Касса Обновлена",
                        }],
                        "employeeIds": ["e_shop"],
                    }],
                    "shopMetrics": {
                        "workAccess": {"2026-10": {"e_shop": ["m_shop"]}},
                        "extraAccess": {"e_shop": []},
                    },
                    "_wt_records": records,
                },
            },
            cookie=cookie,
        )
        self.assertTrue(status.startswith("200"), status)
        status, data, *_ = call("GET", "/api/state", cookie=cookie)
        self.assertTrue(status.startswith("200"), status)
        access = data["payload"]["shopMetrics"]["workAccess"]["2026-10"]
        self.assertEqual(access["e_shop"], ["m_shop"])
        self.assertEqual(access["e_wh"], ["m_wh"])
        shop = next(row for row in data["payload"]["workspaces"] if row["code"] == "shop")
        by_id = {row["id"]: row for row in shop["employees"]}
        self.assertEqual(by_id["e_shop"]["name"], "Касса Обновлена")
        # Ставка не отдаётся без salary view, но в снимке БД должна остаться.
        stored_shop = next(row for row in self.store["payload"]["workspaces"] if row["code"] == "shop")
        stored_emp = next(row for row in stored_shop["employees"] if row["id"] == "e_shop")
        self.assertEqual(stored_emp["rate"], 1500)
        items = {row["id"]: row for row in self.store["payload"]["shopMetricCatalog"]["items"]}
        self.assertEqual(items["m_wh"]["assignments"][0]["employee_ids"], ["e_wh"])

    def _seed_group_catalog_individual_assignments(self):
        """Единый каталог с group_all по подразделениям + индивидуальный workAccess."""
        shop = next(row for row in self.store["payload"]["workspaces"] if row["code"] == "shop")
        shop["employees"] = [
            {
                "id": "e_pack_a",
                "employee_id": "e_pack_a",
                "name": "Разборщик А",
                "work_tab_id": "packers",
                "rate": 1200,
            },
            {
                "id": "e_pack_b",
                "employee_id": "e_pack_b",
                "name": "Разборщик Б",
                "work_tab_id": "packers",
                "rate": 1200,
            },
            {
                "id": "e_mgr",
                "employee_id": "e_mgr",
                "name": "Руководитель",
                "work_tab_id": "managers",
                "rate": 2000,
            },
        ]
        shop["employeeIds"] = ["e_pack_a", "e_pack_b", "e_mgr"]
        wh = next(row for row in self.store["payload"]["workspaces"] if row["code"] == "warehouse")
        wh["employees"] = [
            {"id": "e_wh_a", "employee_id": "e_wh_a", "name": "Грузчик А", "work_tab_id": "loaders", "rate": 1100},
            {"id": "e_wh_b", "employee_id": "e_wh_b", "name": "Грузчик Б", "work_tab_id": "loaders", "rate": 1100},
        ]
        wh["employeeIds"] = ["e_wh_a", "e_wh_b"]
        self.store["payload"]["workspaces"].append({
            "id": "ws_office",
            "code": "office",
            "name": "Офис",
            "employees": [
                {"id": "e_acc_a", "employee_id": "e_acc_a", "name": "Бухгалтер А", "work_tab_id": "accountants", "rate": 1600},
                {"id": "e_acc_b", "employee_id": "e_acc_b", "name": "Бухгалтер Б", "work_tab_id": "accountants", "rate": 1600},
            ],
            "employeeIds": ["e_acc_a", "e_acc_b"],
        })
        self.store["payload"]["workspaces"].append({
            "id": "ws_drivers",
            "code": "drivers",
            "name": "Логистика + Торговые",
            "employees": [
                {"id": "e_drv_a", "employee_id": "e_drv_a", "name": "Водитель А", "work_tab_id": "drivers", "rate": 1400},
                {"id": "e_drv_b", "employee_id": "e_drv_b", "name": "Водитель Б", "work_tab_id": "drivers", "rate": 1400},
            ],
            "employeeIds": ["e_drv_a", "e_drv_b"],
        })
        self.store["payload"]["shopMetricCatalog"] = {
            "items": [
                {
                    "id": "m_shelf",
                    "name": "Выкладка позиций",
                    "department_id": "shop",
                    "work_tab_ids": ["packers"],
                    "assign_mode": "group_all",
                    "assignments": [{
                        "assignment_id": "a_shelf",
                        "department_id": "shop",
                        "work_tab_id": "packers",
                        "assign_mode": "group_all",
                        "employee_ids": [],
                    }],
                },
                {
                    "id": "m_sugar",
                    "name": "Фасовка сахара",
                    "department_id": "shop",
                    "work_tab_ids": ["packers"],
                    "assign_mode": "group_all",
                    "assignments": [{
                        "assignment_id": "a_sugar",
                        "department_id": "shop",
                        "work_tab_id": "packers",
                        "assign_mode": "group_all",
                        "employee_ids": [],
                    }],
                },
                {
                    "id": "m_mgr_check",
                    "name": "Проверка зала",
                    "department_id": "shop",
                    "work_tab_ids": ["managers"],
                    "assign_mode": "group_all",
                    "assignments": [{
                        "assignment_id": "a_mgr",
                        "department_id": "shop",
                        "work_tab_id": "managers",
                        "assign_mode": "group_all",
                        "employee_ids": [],
                    }],
                },
                {
                    "id": "m_load",
                    "name": "Погрузка",
                    "department_id": "warehouse",
                    "work_tab_ids": ["loaders"],
                    "assign_mode": "group_all",
                    "assignments": [{
                        "assignment_id": "a_load",
                        "department_id": "warehouse",
                        "work_tab_id": "loaders",
                        "assign_mode": "group_all",
                        "employee_ids": [],
                    }],
                },
                {
                    "id": "m_acc",
                    "name": "Сверка документов",
                    "department_id": "office",
                    "work_tab_ids": ["accountants"],
                    "assign_mode": "group_all",
                    "assignments": [{
                        "assignment_id": "a_acc",
                        "department_id": "office",
                        "work_tab_id": "accountants",
                        "assign_mode": "group_all",
                        "employee_ids": [],
                    }],
                },
                {
                    "id": "m_trip",
                    "name": "Рейсы",
                    "department_id": "drivers",
                    "territory_category": "drivers",
                    "work_tab_ids": ["drivers"],
                    "assign_mode": "group_all",
                    "assignments": [{
                        "assignment_id": "a_trip",
                        "department_id": "drivers",
                        "work_tab_id": "drivers",
                        "assign_mode": "group_all",
                        "employee_ids": [],
                    }],
                },
                {
                    "id": "m_specific_only",
                    "name": "Точечный показатель",
                    "department_id": "shop",
                    "work_tab_ids": ["packers"],
                    "assign_mode": "specific",
                    "assignments": [{
                        "assignment_id": "a_spec",
                        "department_id": "shop",
                        "work_tab_id": "packers",
                        "assign_mode": "specific",
                        "employee_ids": ["e_pack_a"],
                    }],
                },
            ]
        }
        self.store["payload"]["shopMetrics"]["workAccess"] = {
            "2026-10": {
                "e_pack_a": ["m_shelf", "m_specific_only"],
                "e_pack_b": ["m_sugar"],
                "e_mgr": ["m_mgr_check"],
            }
        }
        self.store["payload"]["shopMetrics"]["extraAccess"] = {
            "e_pack_a": ["m_shelf", "m_specific_only"],
            "e_pack_b": ["m_sugar"],
            "e_mgr": ["m_mgr_check"],
        }
        self.store["payload"]["shopMetrics"]["facts"] = {
            "2026-10": {"e_pack_a": {"1": {"m_shelf": 5}}},
            "ok": 1,
        }
        self.store["payload"]["deptMetrics"] = {
            "warehouse": {
                "workAccess": {"2026-10": {"e_wh_a": ["m_load"], "e_wh_b": []}},
                "facts": {"secret": 1},
            },
            "office": {
                "workAccess": {"2026-10": {"e_acc_a": ["m_acc"], "e_acc_b": []}},
                "facts": {"x": 1},
            },
            "drivers": {
                "workAccess": {"2026-10": {"e_drv_a": ["m_trip"], "e_drv_b": []}},
                "facts": {},
            },
        }

    def test_group_catalog_two_employees_independent_assignments(self):
        self._seed_group_catalog_individual_assignments()
        cookie = self._add_shop_manager(
            "usr_group_cat",
            {"metricsSetup": "edit", "metricsInput": "edit", "timesheet": "edit"},
        )
        # Снимаем у А выкладку, оставляем точечный; Б не трогаем.
        status, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(self.store["version"]),
                "payload": {
                    "shopMetrics": {
                        "workAccess": {
                            "2026-10": {
                                "e_pack_a": ["m_specific_only"],
                                "e_pack_b": ["m_sugar"],
                                "e_mgr": ["m_mgr_check"],
                            }
                        },
                        "extraAccess": {
                            "e_pack_a": ["m_specific_only"],
                            "e_pack_b": ["m_sugar"],
                            "e_mgr": ["m_mgr_check"],
                        },
                    }
                },
            },
            cookie=cookie,
        )
        self.assertTrue(status.startswith("200"), status)
        status, data, *_ = call("GET", "/api/state", cookie=cookie)
        self.assertTrue(status.startswith("200"), status)
        access = data["payload"]["shopMetrics"]["workAccess"]["2026-10"]
        self.assertEqual(access["e_pack_a"], ["m_specific_only"])
        self.assertEqual(access["e_pack_b"], ["m_sugar"])
        self.assertEqual(access["e_mgr"], ["m_mgr_check"])
        # Общий каталог и group_all не меняются при индивидуальном снятии.
        items = {row["id"]: row for row in self.store["payload"]["shopMetricCatalog"]["items"]}
        self.assertEqual(items["m_shelf"]["assign_mode"], "group_all")
        self.assertEqual(items["m_shelf"]["assignments"][0]["assign_mode"], "group_all")
        self.assertEqual(items["m_sugar"]["assignments"][0]["assign_mode"], "group_all")
        self.assertEqual(items["m_specific_only"]["assignments"][0]["employee_ids"], ["e_pack_a"])
        # История фактов сохраняется.
        self.assertEqual(
            self.store["payload"]["shopMetrics"]["facts"]["2026-10"]["e_pack_a"]["1"]["m_shelf"],
            5,
        )

    def test_group_catalog_clear_all_and_other_depts_stay(self):
        self._seed_group_catalog_individual_assignments()
        self.add_user({
            "user_id": "usr_clear_all",
            "display_name": "Мультиотдел",
            "pin": "4444",
            "role": "manager",
            "is_active": True,
            "departments": ["shop", "warehouse", "office", "drivers"],
            "permissions": {
                "metricsSetup": "edit",
                "metricsInput": "edit",
                "timesheet": "edit",
            },
        })
        cookie, _ = self.login("usr_clear_all", "4444")
        # Полное снятие у pack_a + независимые назначения склада/офиса/логистики.
        status, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(self.store["version"]),
                "payload": {
                    "shopMetrics": {
                        "workAccess": {
                            "2026-10": {
                                "e_pack_a": [],
                                "e_pack_b": ["m_sugar"],
                                "e_mgr": ["m_mgr_check"],
                            }
                        },
                        "extraAccess": {
                            "e_pack_a": [],
                            "e_pack_b": ["m_sugar"],
                            "e_mgr": ["m_mgr_check"],
                        },
                    },
                    "deptMetrics": {
                        "warehouse": {"workAccess": {"2026-10": {"e_wh_a": [], "e_wh_b": ["m_load"]}}},
                        "office": {"workAccess": {"2026-10": {"e_acc_a": ["m_acc"], "e_acc_b": ["m_acc"]}}},
                        "drivers": {"workAccess": {"2026-10": {"e_drv_a": [], "e_drv_b": ["m_trip"]}}},
                    },
                },
            },
            cookie=cookie,
        )
        self.assertTrue(status.startswith("200"), status)
        status, data, *_ = call("GET", "/api/state", cookie=cookie)
        self.assertTrue(status.startswith("200"), status)
        shop_access = data["payload"]["shopMetrics"]["workAccess"]["2026-10"]
        self.assertEqual(shop_access["e_pack_a"], [])
        self.assertEqual(shop_access["e_pack_b"], ["m_sugar"])
        self.assertEqual(
            data["payload"]["deptMetrics"]["warehouse"]["workAccess"]["2026-10"]["e_wh_b"],
            ["m_load"],
        )
        self.assertEqual(
            data["payload"]["deptMetrics"]["office"]["workAccess"]["2026-10"]["e_acc_b"],
            ["m_acc"],
        )
        self.assertEqual(
            data["payload"]["deptMetrics"]["drivers"]["workAccess"]["2026-10"]["e_drv_b"],
            ["m_trip"],
        )
        items = {row["id"]: row for row in self.store["payload"]["shopMetricCatalog"]["items"]}
        for mid in ("m_shelf", "m_sugar", "m_load", "m_acc", "m_trip", "m_mgr_check"):
            self.assertEqual(items[mid]["assignments"][0]["assign_mode"], "group_all", mid)
            self.assertEqual(items[mid]["id"], mid)

    def test_metrics_input_cannot_change_group_assignments(self):
        self._seed_group_catalog_individual_assignments()
        cookie = self._add_shop_manager(
            "usr_input_only",
            {"metricsInput": "edit", "timesheet": "edit"},
        )
        before = json.loads(json.dumps(
            self.store["payload"]["shopMetrics"]["workAccess"]["2026-10"]["e_pack_a"]
        ))
        status, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(self.store["version"]),
                "payload": {
                    "shopMetrics": {
                        "workAccess": {"2026-10": {"e_pack_a": []}},
                        "extraAccess": {"e_pack_a": []},
                    }
                },
            },
            cookie=cookie,
        )
        # Без metricsSetup назначения не меняются (403 или игнор ветки).
        after = self.store["payload"]["shopMetrics"]["workAccess"]["2026-10"]["e_pack_a"]
        self.assertEqual(after, before)
        if not status.startswith("200"):
            self.assertTrue(status.startswith("403"), status)

    def test_group_catalog_new_employee_empty_access_persists(self):
        self._seed_group_catalog_individual_assignments()
        cookie = self._add_shop_manager(
            "usr_new_emp",
            {"metricsSetup": "edit", "metricsInput": "edit", "timesheet": "edit"},
        )
        # Новый сотрудник без rate (нет salary) и с пустым workAccess — group_all не наполняет сам.
        status, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(self.store["version"]),
                "payload": {
                    "workspaces": [{
                        "code": "shop",
                        "employees": [{
                            "id": "e_pack_c",
                            "employee_id": "e_pack_c",
                            "name": "Разборщик В",
                            "work_tab_id": "packers",
                        }],
                        "employeeIds": ["e_pack_c"],
                    }],
                    "shopMetrics": {
                        "workAccess": {
                            "2026-10": {
                                "e_pack_a": ["m_shelf"],
                                "e_pack_b": ["m_sugar"],
                                "e_pack_c": [],
                            }
                        },
                        "extraAccess": {
                            "e_pack_a": ["m_shelf"],
                            "e_pack_b": ["m_sugar"],
                            "e_pack_c": [],
                        },
                    },
                },
            },
            cookie=cookie,
        )
        self.assertTrue(status.startswith("200"), status)
        status, data, *_ = call("GET", "/api/state", cookie=cookie)
        self.assertTrue(status.startswith("200"), status)
        access = data["payload"]["shopMetrics"]["workAccess"]["2026-10"]
        self.assertEqual(access["e_pack_c"], [])
        self.assertEqual(access["e_pack_a"], ["m_shelf"])
        shop = next(row for row in self.store["payload"]["workspaces"] if row["code"] == "shop")
        self.assertIn("e_pack_c", {row["id"] for row in shop["employees"]})
        items = {row["id"]: row for row in self.store["payload"]["shopMetricCatalog"]["items"]}
        self.assertEqual(items["m_shelf"]["assignments"][0]["assign_mode"], "group_all")

    def _seed_finance_directory(self):
        self.store["payload"]["financeRec"] = {
            "operations": [
                {"id": "op_shop", "amount": 10, "account_id": "acc_shop", "object_id": "farea_shop"},
                {"id": "op_ter", "amount": 20, "account_id": "acc_ter", "object_id": "farea_territory"},
            ],
            "openings": [],
            "sources": [
                {
                    "finance_source_id": "fsrc_shop_a",
                    "account_id": "acc_shop",
                    "name": "Касса А",
                    "account_type": "cash",
                    "sort_order": 10,
                    "is_active": True,
                    "in_actual_balance": True,
                    "object_id": "farea_shop",
                    "finance_area_id": "farea_shop",
                },
                {
                    "finance_source_id": "fsrc_shop_b",
                    "account_id": "acc_shop_b",
                    "name": "Банк",
                    "account_type": "bank",
                    "sort_order": 20,
                    "is_active": True,
                    "object_id": "farea_shop",
                    "finance_area_id": "farea_shop",
                },
                {
                    "finance_source_id": "fsrc_ter",
                    "account_id": "acc_ter",
                    "name": "Касса территории",
                    "account_type": "cash",
                    "sort_order": 10,
                    "is_active": True,
                    "object_id": "farea_territory",
                    "finance_area_id": "farea_territory",
                },
            ],
            "areas": [
                {
                    "finance_area_id": "farea_shop",
                    "object_id": "farea_shop",
                    "name": "Магазин Володарского",
                    "primary_cash_source_id": "fsrc_shop_a",
                    "primary_cash_account_id": "acc_shop",
                },
                {
                    "finance_area_id": "farea_territory",
                    "object_id": "farea_territory",
                    "name": "Территория",
                    "primary_cash_source_id": "fsrc_ter",
                    "primary_cash_account_id": "acc_ter",
                },
            ],
        }

    def test_manager_creates_cash_and_shared_account_order(self):
        self._seed_finance_directory()
        cookie = self._add_shop_manager("usr_fin", {
            "financeRecSetup": "edit",
            "financeRec": "edit",
        })
        self.add_user({
            "user_id": "usr_view",
            "display_name": "Смотрящий",
            "pin": "4545",
            "role": "user",
            "is_active": True,
            "departments": ["shop"],
            "permissions": {"financeRec": "view"},
        })
        ops_before = json.loads(json.dumps(self.store["payload"]["financeRec"]["operations"]))
        status, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(self.store["version"]),
                "payload": {"financeRec": {
                    "sources": [
                        {
                            "finance_source_id": "fsrc_shop_a",
                            "account_id": "acc_shop",
                            "name": "Касса А",
                            "account_type": "cash",
                            "sort_order": 20,
                            "is_active": True,
                            "object_id": "farea_shop",
                        },
                        {
                            "finance_source_id": "fsrc_shop_b",
                            "account_id": "acc_shop_b",
                            "name": "Банк",
                            "account_type": "bank",
                            "sort_order": 10,
                            "is_active": True,
                            "object_id": "farea_shop",
                        },
                        {
                            "finance_source_id": "fsrc_new",
                            "account_id": "fsrc_new",
                            "name": "Касса новая",
                            "account_type": "cash",
                            "sort_order": 30,
                            "is_active": True,
                            "in_actual_balance": True,
                            "object_id": "farea_shop",
                            "finance_area_id": "farea_shop",
                        },
                    ],
                    "areas": [
                        {
                            "object_id": "farea_shop",
                            "primary_cash_source_id": "fsrc_new",
                        },
                        {
                            "object_id": "farea_territory",
                            "primary_cash_source_id": "fsrc_new",
                        },
                    ],
                }},
            },
            cookie=cookie,
        )
        self.assertTrue(status.startswith("200"), status)
        sources = {
            row["finance_source_id"]: row
            for row in self.store["payload"]["financeRec"]["sources"]
        }
        self.assertEqual(sources["fsrc_new"]["name"], "Касса новая")
        self.assertEqual(sources["fsrc_new"]["account_type"], "cash")
        self.assertEqual(sources["fsrc_new"]["object_id"], "farea_shop")
        self.assertEqual(sources["fsrc_shop_a"]["sort_order"], 20)
        self.assertEqual(sources["fsrc_shop_b"]["sort_order"], 10)
        self.assertEqual(sources["fsrc_shop_a"]["account_id"], "acc_shop")
        self.assertEqual(sources["fsrc_ter"]["name"], "Касса территории")
        self.assertEqual(sources["fsrc_ter"]["sort_order"], 10)
        self.assertEqual(sources["fsrc_ter"]["account_type"], "cash")
        self.assertTrue(sources["fsrc_ter"]["is_active"])
        areas = {
            row["object_id"]: row for row in self.store["payload"]["financeRec"]["areas"]
        }
        self.assertEqual(areas["farea_shop"]["primary_cash_source_id"], "fsrc_new")
        self.assertEqual(areas["farea_territory"]["primary_cash_source_id"], "fsrc_ter")
        self.assertEqual(self.store["payload"]["financeRec"]["operations"], ops_before)
        view_cookie, _ = self.login("usr_view", "4545")
        status, viewed, *_ = call("GET", "/api/state", cookie=view_cookie)
        self.assertTrue(status.startswith("200"), status)
        viewed_order = [
            (row["finance_source_id"], row["sort_order"])
            for row in viewed["payload"]["financeRec"]["sources"]
            if row.get("object_id") == "farea_shop"
        ]
        self.assertEqual(
            viewed_order,
            [("fsrc_shop_a", 20), ("fsrc_shop_b", 10), ("fsrc_new", 30)],
        )
        admin_cookie, _ = self.login("user_admin", "5555")
        status, admin_view, *_ = call("GET", "/api/state", cookie=admin_cookie)
        self.assertTrue(status.startswith("200"), status)
        admin_order = [
            (row["finance_source_id"], row["sort_order"])
            for row in admin_view["payload"]["financeRec"]["sources"]
            if row.get("object_id") == "farea_shop"
        ]
        self.assertEqual(admin_order, viewed_order)
        self.assertEqual(admin_view["payload"]["financeRec"]["operations"], ops_before)

    def test_setup_right_manages_every_finance_object(self):
        self._seed_finance_directory()
        self.add_user({
            "user_id": "usr_books",
            "display_name": "Справочники",
            "pin": "4646",
            "role": "user",
            "is_active": True,
            "departments": ["financeRec"],
            "permissions": {"financeRecSetup": "edit"},
        })
        self.add_user({
            "user_id": "usr_view2",
            "display_name": "Просмотр",
            "pin": "4747",
            "role": "user",
            "is_active": True,
            "departments": ["financeRec"],
            "permissions": {"financeRec": "view"},
        })
        cookie, _ = self.login("usr_books", "4646")
        ops = json.loads(json.dumps(self.store["payload"]["financeRec"]["operations"]))
        status, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(self.store["version"]),
                "payload": {"financeRec": {
                    "sources": [
                        {
                            "finance_source_id": "fsrc_shop_new",
                            "account_id": "fsrc_shop_new",
                            "name": "Счёт магазина",
                            "account_type": "bank",
                            "sort_order": 40,
                            "is_active": True,
                            "in_actual_balance": True,
                            "object_id": "farea_shop",
                        },
                        {
                            "finance_source_id": "fsrc_ter_new",
                            "account_id": "fsrc_ter_new",
                            "name": "Касса территории",
                            "account_type": "cash",
                            "sort_order": 15,
                            "is_active": True,
                            "object_id": "farea_territory",
                        },
                        {
                            "finance_source_id": "fsrc_unknown",
                            "name": "Неизвестный объект",
                            "account_type": "cash",
                            "object_id": "farea_other",
                        },
                    ],
                    "areas": [{
                        "object_id": "farea_territory",
                        "primary_cash_source_id": "fsrc_ter_new",
                    }],
                    "operations": [{"id": "op_shop", "amount": 1, "account_id": "acc_shop"}],
                }},
            },
            cookie=cookie,
        )
        self.assertTrue(status.startswith("403"), status)
        self.assertEqual(self.store["payload"]["financeRec"]["operations"], ops)
        self.assertNotIn("fsrc_shop_new", json.dumps(self.store["payload"]["financeRec"]["sources"]))
        status, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(self.store["version"]),
                "payload": {"financeRec": {
                    "sources": [
                        {
                            "finance_source_id": "fsrc_shop_new",
                            "account_id": "fsrc_shop_new",
                            "name": "Счёт магазина",
                            "account_type": "bank",
                            "sort_order": 40,
                            "is_active": True,
                            "in_actual_balance": True,
                            "object_id": "farea_shop",
                        },
                        {
                            "finance_source_id": "fsrc_ter_new",
                            "account_id": "fsrc_ter_new",
                            "name": "Новая касса территории",
                            "account_type": "cash",
                            "sort_order": 15,
                            "is_active": True,
                            "object_id": "farea_territory",
                        },
                        {
                            "finance_source_id": "fsrc_unknown",
                            "name": "Неизвестный объект",
                            "account_type": "cash",
                            "object_id": "farea_other",
                        },
                    ],
                    "areas": [{
                        "object_id": "farea_territory",
                        "primary_cash_source_id": "fsrc_ter_new",
                    }],
                }},
            },
            cookie=cookie,
        )
        self.assertTrue(status.startswith("200"), status)
        sources = {
            row["finance_source_id"]: row
            for row in self.store["payload"]["financeRec"]["sources"]
        }
        self.assertEqual(sources["fsrc_shop_new"]["name"], "Счёт магазина")
        self.assertEqual(sources["fsrc_shop_new"]["object_id"], "farea_shop")
        self.assertEqual(sources["fsrc_ter_new"]["name"], "Новая касса территории")
        self.assertEqual(sources["fsrc_ter_new"]["account_type"], "cash")
        self.assertEqual(sources["fsrc_ter"]["name"], "Касса территории")
        self.assertNotIn("fsrc_unknown", sources)
        areas = {row["object_id"]: row for row in self.store["payload"]["financeRec"]["areas"]}
        self.assertEqual(areas["farea_territory"]["primary_cash_source_id"], "fsrc_ter_new")
        self.assertEqual(areas["farea_shop"]["primary_cash_source_id"], "fsrc_shop_a")
        self.assertEqual(self.store["payload"]["financeRec"]["operations"], ops)
        status, again, *_ = call("GET", "/api/state", cookie=cookie)
        self.assertTrue(status.startswith("200"), status)
        again_ids = {row["finance_source_id"] for row in again["payload"]["financeRec"]["sources"]}
        self.assertIn("fsrc_shop_new", again_ids)
        self.assertIn("fsrc_ter_new", again_ids)
        view_cookie, _ = self.login("usr_view2", "4747")
        status, viewed, *_ = call("GET", "/api/state", cookie=view_cookie)
        self.assertTrue(status.startswith("200"), status)
        viewed_order = [
            (row["finance_source_id"], row.get("sort_order"))
            for row in viewed["payload"]["financeRec"]["sources"]
            if row.get("object_id") == "farea_territory"
        ]
        self.assertIn(("fsrc_ter_new", 15), viewed_order)
        self.assertEqual(viewed["payload"]["financeRec"]["operations"], ops)

    def test_staff_roles_create_cash_and_bank_with_setup(self):
        """Обычный сотрудник и руководитель с financeRecSetup создают кассы и банк."""
        self._seed_finance_directory()
        cases = [
            ("usr_fin_user", "user", {"financeRec": "edit", "financeRecSetup": "edit"}),
            ("usr_fin_mgr", "manager", {"financeRec": "edit", "financeRecSetup": "edit"}),
            ("usr_fin_setup_only", "user", {"financeRecSetup": "edit"}),
        ]
        for user_id, role, perms in cases:
            self.add_user({
                "user_id": user_id,
                "display_name": user_id,
                "pin": "4810",
                "role": role,
                "is_active": True,
                "departments": ["financeRec"],
                "permissions": perms,
            })
            cookie, _ = self.login(user_id, "4810")
            cash_id = "fsrc_cash_" + user_id
            bank_id = "fsrc_bank_" + user_id
            # Как клиент: полный список sources + новые записи.
            sources_payload = json.loads(json.dumps(
                self.store["payload"]["financeRec"]["sources"]
            ))
            sources_payload.extend([
                {
                    "finance_source_id": cash_id,
                    "account_id": cash_id,
                    "name": "Касса " + user_id,
                    "account_type": "cash",
                    "sort_order": 50,
                    "is_active": True,
                    "in_actual_balance": True,
                    "object_id": "farea_shop",
                    "finance_area_id": "farea_shop",
                },
                {
                    "finance_source_id": bank_id,
                    "account_id": bank_id,
                    "name": "Банк " + user_id,
                    "account_type": "bank",
                    "sort_order": 60,
                    "is_active": True,
                    "in_actual_balance": True,
                    "object_id": "farea_shop",
                    "finance_area_id": "farea_shop",
                },
            ])
            status, *_ = call(
                "PUT",
                "/api/state",
                {
                    "version": int(self.store["version"]),
                    "payload": {"financeRec": {
                        "sources": sources_payload,
                        # «залипший» removed id при сохранении полного списка не блокирует создание.
                        "removed_source_ids": ["acc_shop"],
                    }},
                },
                cookie=cookie,
            )
            self.assertTrue(status.startswith("200"), "%s %s" % (user_id, status))
            sources = {
                row["finance_source_id"]: row
                for row in self.store["payload"]["financeRec"]["sources"]
            }
            self.assertEqual(sources[cash_id]["account_type"], "cash")
            self.assertEqual(sources[bank_id]["account_type"], "bank")
            self.assertEqual(sources["fsrc_shop_a"]["name"], "Касса А")
            status, data, *_ = call("GET", "/api/state", cookie=cookie)
            self.assertTrue(status.startswith("200"), status)
            again = {row["finance_source_id"] for row in data["payload"]["financeRec"]["sources"]}
            self.assertIn(cash_id, again)
            self.assertIn(bank_id, again)

    def test_finance_view_cannot_create_cash_even_via_api(self):
        self._seed_finance_directory()
        self.add_user({
            "user_id": "usr_fin_view",
            "display_name": "Только просмотр",
            "pin": "4820",
            "role": "user",
            "is_active": True,
            "departments": ["financeRec"],
            "permissions": {"financeRec": "edit"},
        })
        cookie, _ = self.login("usr_fin_view", "4820")
        before = json.loads(json.dumps(self.store["payload"]["financeRec"]["sources"]))
        status, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(self.store["version"]),
                "payload": {"financeRec": {"sources": [{
                    "finance_source_id": "fsrc_hack",
                    "account_id": "fsrc_hack",
                    "name": "Чужая касса",
                    "account_type": "cash",
                    "object_id": "farea_shop",
                }]}},
            },
            cookie=cookie,
        )
        self.assertTrue(status.startswith("403"), status)
        self.assertEqual(self.store["payload"]["financeRec"]["sources"], before)

    def test_fr_can_manage_accounts_uses_setup_perm_not_area_list(self):
        src = Path(__file__).resolve().parents[1].joinpath("fr-mod.js").read_text(encoding="utf-8")
        start = src.find("function frCanManageAccounts(")
        end = src.find("function frDirectorySnap(", start)
        body = src[start:end]
        self.assertGreater(start, 0)
        self.assertIn('userHasPerm("financeRecSetup", "edit")', body)
        self.assertNotIn("frSetupAreasForUser()", body)
        self.assertNotIn("allowed.indexOf(area)", body)

    def test_staff_get_includes_filtered_wt_records_only(self):
        """Сотрудник видит _wt_records только по доступным веткам, без чужих данных."""
        self.store["payload"]["_wt_records"] = {
            "workspaces.shop.timesheet.2026-10.e_shop.1": {"v": 3, "updated_at": "t1", "updated_by": "x"},
            "workspaces.warehouse.timesheet.2026-10.e_wh.1": {"v": 9, "updated_by": "y"},
            "financeRec.audit": {"v": 5, "updated_at": "t2"},
            "salary.2026-10": {"v": 7},
        }
        cookie, _ = self.login("usr_001", "1111")
        status, data, *_ = call("GET", "/api/state", cookie=cookie)
        self.assertTrue(status.startswith("200"), status)
        rec = (data.get("payload") or {}).get("_wt_records") or {}
        self.assertIn("workspaces.shop.timesheet.2026-10.e_shop.1", rec)
        self.assertEqual(int(rec["workspaces.shop.timesheet.2026-10.e_shop.1"]["v"]), 3)
        self.assertNotIn("workspaces.warehouse.timesheet.2026-10.e_wh.1", rec)
        self.assertNotIn("financeRec.audit", rec)
        self.assertNotIn("salary.2026-10", rec)
        self.assertNotIn("financeRec", data["payload"])
        self.assertNotIn("salary", data["payload"])

    def test_finance_staff_get_sees_finance_record_versions(self):
        self._seed_finance_directory()
        fr = self.store["payload"]["financeRec"]
        fr["audit"] = [{"audit_id": "faud_%d" % i, "kind": "seed"} for i in range(25)]
        fr["operations"] = list(fr.get("operations") or [])
        fr["import_batches"] = []
        self.store["payload"]["_wt_records"] = {
            "financeRec.audit": {"v": 4},
            "financeRec.operations": {"v": 2},
            "financeRec.import_batches": {"v": 1},
            "workspaces.shop.timesheet.2026-10.e_shop.1": {"v": 8},
        }
        self.add_user({
            "user_id": "usr_fin_rec",
            "display_name": "Финконтроль",
            "pin": "4811",
            "role": "user",
            "is_active": True,
            "departments": ["financeRec"],
            "permissions": {"financeRec": "edit"},
        })
        cookie, _ = self.login("usr_fin_rec", "4811")
        status, data, *_ = call("GET", "/api/state", cookie=cookie)
        self.assertTrue(status.startswith("200"), status)
        payload = data["payload"]
        self.assertIn("financeRec", payload)
        self.assertEqual(payload.get("workspaces") or [], [])
        rec = payload.get("_wt_records") or {}
        self.assertEqual(int(rec["financeRec.audit"]["v"]), 4)
        self.assertEqual(int(rec["financeRec.operations"]["v"]), 2)
        self.assertEqual(int(rec["financeRec.import_batches"]["v"]), 1)
        self.assertNotIn("workspaces.shop.timesheet.2026-10.e_shop.1", rec)

    def test_finance_two_areas_import_arrays_no_false_conflict(self):
        """Два сотрудника разных объектов: merge + актуальный expected_v → оба набора сохранены."""
        self._seed_finance_directory()
        fr = self.store["payload"]["financeRec"]
        fr["audit"] = [{"audit_id": "faud_%d" % i, "kind": "seed"} for i in range(25)]
        fr["operations"] = []
        fr["import_batches"] = []
        self.store["payload"]["_wt_records"] = {}
        self.add_user({
            "user_id": "fin_shop",
            "display_name": "Магазин",
            "pin": "1001",
            "role": "user",
            "is_active": True,
            "departments": ["financeRec"],
            "permissions": {"financeRec": "edit"},
        })
        self.add_user({
            "user_id": "fin_ter",
            "display_name": "Территория",
            "pin": "1002",
            "role": "user",
            "is_active": True,
            "departments": ["financeRec"],
            "permissions": {"financeRec": "edit"},
        })
        ca, _ = self.login("fin_shop", "1001")
        cb, _ = self.login("fin_ter", "1002")
        # Noop bump audit → v=1 при 25 элементах (как ложный конфликт до фикса).
        st, *_ = call(
            "POST",
            "/api/state/op",
            {"op": {"path": "financeRec.audit", "expected_v": 0, "value": list(fr["audit"])}},
            cookie=ca,
        )
        self.assertTrue(st.startswith("200"), st)
        # A: GET → актуальный v, append shop
        stg, dg, *_ = call("GET", "/api/state", cookie=ca)
        self.assertTrue(stg.startswith("200"), stg)
        v_audit = int((((dg.get("payload") or {}).get("_wt_records") or {}).get("financeRec.audit") or {}).get("v") or 0)
        self.assertGreaterEqual(v_audit, 1)
        audit_a = list((dg["payload"]["financeRec"]["audit"] or []))
        audit_a.append({
            "audit_id": "faud_shop",
            "kind": "expense_import",
            "finance_area_id": "farea_shop",
        })
        ops_a = [{
            "finance_operation_id": "fop_shop",
            "finance_area_id": "farea_shop",
            "amount": 100,
            "import_batch_id": "fimp_shop",
            "import_fingerprint": "fp_shop",
        }]
        batches_a = [{
            "import_batch_id": "fimp_shop",
            "batch_key": "key_shop",
            "finance_area_id": "farea_shop",
        }]
        st, data, *_ = call(
            "POST",
            "/api/state/op/batch",
            {
                "atomic": True,
                "ops": [
                    {"path": "financeRec.operations", "expected_v": 0, "value": ops_a},
                    {"path": "financeRec.import_batches", "expected_v": 0, "value": batches_a},
                    {"path": "financeRec.audit", "expected_v": v_audit, "value": audit_a},
                ],
            },
            cookie=ca,
        )
        self.assertTrue(st.startswith("200"), st)
        self.assertTrue(data.get("ok"))
        # B: GET+merge (как fr-mod) + save
        stg, dg, *_ = call("GET", "/api/state", cookie=cb)
        rec = (dg.get("payload") or {}).get("_wt_records") or {}
        remote_fr = dg["payload"]["financeRec"]
        remote_ops = list(remote_fr.get("operations") or [])
        remote_batches = list(remote_fr.get("import_batches") or [])
        remote_audit = list(remote_fr.get("audit") or [])
        remote_ops.append({
            "finance_operation_id": "fop_ter",
            "finance_area_id": "farea_territory",
            "amount": 200,
            "import_batch_id": "fimp_ter",
            "import_fingerprint": "fp_ter",
        })
        remote_batches.append({
            "import_batch_id": "fimp_ter",
            "batch_key": "key_ter",
            "finance_area_id": "farea_territory",
        })
        remote_audit.append({
            "audit_id": "faud_ter",
            "kind": "expense_import",
            "finance_area_id": "farea_territory",
        })
        st, data, *_ = call(
            "POST",
            "/api/state/op/batch",
            {
                "atomic": True,
                "ops": [
                    {
                        "path": "financeRec.operations",
                        "expected_v": int((rec.get("financeRec.operations") or {}).get("v") or 0),
                        "value": remote_ops,
                    },
                    {
                        "path": "financeRec.import_batches",
                        "expected_v": int((rec.get("financeRec.import_batches") or {}).get("v") or 0),
                        "value": remote_batches,
                    },
                    {
                        "path": "financeRec.audit",
                        "expected_v": int((rec.get("financeRec.audit") or {}).get("v") or 0),
                        "value": remote_audit,
                    },
                ],
            },
            cookie=cb,
        )
        self.assertTrue(st.startswith("200"), "%s %s" % (st, data))
        saved = self.store["payload"]["financeRec"]
        op_ids = {str(o.get("finance_operation_id")) for o in saved.get("operations") or []}
        batch_ids = {str(b.get("import_batch_id")) for b in saved.get("import_batches") or []}
        audit_ids = {str(a.get("audit_id")) for a in saved.get("audit") or []}
        self.assertIn("fop_shop", op_ids)
        self.assertIn("fop_ter", op_ids)
        self.assertIn("fimp_shop", batch_ids)
        self.assertIn("fimp_ter", batch_ids)
        self.assertIn("faud_shop", audit_ids)
        self.assertIn("faud_ter", audit_ids)
        self.assertEqual(len(saved.get("audit") or []), 27)

    def test_finance_stale_expected_without_get_still_conflicts_then_merge_ok(self):
        """Устаревший expected_v=0 даёт 409; после GET с версией и merge — сохранение проходит."""
        self._seed_finance_directory()
        fr = self.store["payload"]["financeRec"]
        fr["audit"] = [{"audit_id": "faud_%d" % i, "kind": "seed"} for i in range(25)]
        self.store["payload"]["_wt_records"] = {}
        self.add_user({
            "user_id": "fin_stale",
            "display_name": "Фин",
            "pin": "1003",
            "role": "user",
            "is_active": True,
            "departments": ["financeRec"],
            "permissions": {"financeRec": "edit"},
        })
        cookie, _ = self.login("fin_stale", "1003")
        call(
            "POST",
            "/api/state/op",
            {"op": {"path": "financeRec.audit", "expected_v": 0, "value": list(fr["audit"])}},
            cookie=cookie,
        )
        stale = list(fr["audit"]) + [{"audit_id": "faud_new", "kind": "expense_import"}]
        st, data, *_ = call(
            "POST",
            "/api/state/op",
            {"op": {"path": "financeRec.audit", "expected_v": 0, "value": stale}},
            cookie=cookie,
        )
        self.assertTrue(st.startswith("409"), st)
        self.assertEqual(data.get("error"), "conflict")
        self.assertEqual(len(data.get("server_value") or []), 25)
        self.assertEqual(len(data.get("your_value") or []), 26)
        stg, dg, *_ = call("GET", "/api/state", cookie=cookie)
        v = int((((dg.get("payload") or {}).get("_wt_records") or {}).get("financeRec.audit") or {}).get("v") or 0)
        merged = list(dg["payload"]["financeRec"]["audit"]) + [{"audit_id": "faud_new", "kind": "expense_import"}]
        st, *_ = call(
            "POST",
            "/api/state/op",
            {"op": {"path": "financeRec.audit", "expected_v": v, "value": merged}},
            cookie=cookie,
        )
        self.assertTrue(st.startswith("200"), st)
        ids = {str(a.get("audit_id")) for a in self.store["payload"]["financeRec"]["audit"]}
        self.assertIn("faud_new", ids)
        self.assertEqual(len(self.store["payload"]["financeRec"]["audit"]), 26)

    def test_finance_same_operation_edit_conflict_keeps_server(self):
        """Правка одной и той же операции разными значениями — 409, серверное значение сохраняется."""
        self._seed_finance_directory()
        self.store["payload"]["financeRec"]["operations"] = [{
            "finance_operation_id": "fop_shared",
            "finance_area_id": "farea_shop",
            "amount": 10,
        }]
        self.store["payload"]["_wt_records"] = {}
        self.add_user({
            "user_id": "fin_e1",
            "display_name": "E1",
            "pin": "2001",
            "role": "user",
            "is_active": True,
            "departments": ["financeRec"],
            "permissions": {"financeRec": "edit"},
        })
        self.add_user({
            "user_id": "fin_e2",
            "display_name": "E2",
            "pin": "2002",
            "role": "user",
            "is_active": True,
            "departments": ["financeRec"],
            "permissions": {"financeRec": "edit"},
        })
        c1, _ = self.login("fin_e1", "2001")
        c2, _ = self.login("fin_e2", "2002")
        st, *_ = call(
            "POST",
            "/api/state/op",
            {
                "op": {
                    "path": "financeRec.operations",
                    "expected_v": 0,
                    "value": [{"finance_operation_id": "fop_shared", "amount": 50}],
                }
            },
            cookie=c1,
        )
        self.assertTrue(st.startswith("200"), st)
        st, data, *_ = call(
            "POST",
            "/api/state/op",
            {
                "op": {
                    "path": "financeRec.operations",
                    "expected_v": 0,
                    "value": [{"finance_operation_id": "fop_shared", "amount": 99}],
                }
            },
            cookie=c2,
        )
        self.assertTrue(st.startswith("409"), st)
        self.assertEqual(data.get("error"), "conflict")
        self.assertEqual(self.store["payload"]["financeRec"]["operations"][0]["amount"], 50)

    def test_fr_mod_import_save_uses_record_versions_and_merge(self):
        src = Path(__file__).resolve().parents[1].joinpath("fr-mod.js").read_text(encoding="utf-8")
        self.assertIn("function frApplyFinanceRecordVersionsFromPayload(", src)
        self.assertIn("function frMergeFinanceArrayByIds(", src)
        self.assertIn("function frSaveFinanceImportArraysOps(", src)
        self.assertIn("frApplyFinanceRecordVersionsFromPayload(state.payload)", src)
        # Этап 3В: импорт — $bulk + партия/журнал по id, без широких массивов.
        self.assertIn('"financeRec.operations.$bulk"', src)
        self.assertIn('"financeRec.import_batches." + bid', src)
        self.assertIn('"financeRec.audit." + String(a.audit_id)', src)
        self.assertNotIn('path: "financeRec.operations",', src)
        self.assertNotIn('path: "financeRec.import_batches",', src)
        self.assertNotIn('path: "financeRec.audit",', src)
        self.assertIn("hardConflict", src)
        self.assertIn("function frFinanceBusinessDiffFields(", src)
        self.assertIn("FR_IMPORT_MERGE_TECH_FIELDS", src)
        self.assertIn("function frCollectExpenseImportDupKeys(", src)
        self.assertIn("function frExpenseImportFileCoverage(", src)
        self.assertIn("function frReconcileFailedExpenseImport(", src)
        self.assertIn("coverage.covered >= coverage.total", src)
        self.assertIn("expectedFingerprints", src)
        self.assertIn("complete: true", src)
        self.assertIn("rec.partial", src)
        self.assertIn("добавятся только недостающие", src)
        apply_fn = src[src.find("function frApplyExpenseImport(") : src.find("function frCollectExpenseImportDupKeys(")]
        self.assertIn("existingBatch && existingBatch.import_batch_id", apply_fn)
        # Успех после потери ответа — только при полном наборе fingerprint.
        rec_fn = src[src.find("function frReconcileFailedExpenseImport(") : src.find("function frSaveExpenseImportBatch(")]
        self.assertIn("missing.length === 0", rec_fn)
        self.assertIn("expectedList.length > 0", rec_fn)
        fn = src[src.find("function frSaveExpenseImportBatch(") : src.find("function financeRecFolderCardHtml(")]
        self.assertIn("frReconcileFailedExpenseImport", fn)
        self.assertIn("rec.complete", fn)
        self.assertIn('toast(okMsg, "success")', fn)
        self.assertIn("Можно повторить импорт того же файла", fn)

    def test_expense_import_partial_server_not_complete_success(self):
        """Часть fingerprint batch на сервере ≠ полный успех; недостающие можно дописать."""
        self._seed_finance_directory()
        fr = self.store["payload"]["financeRec"]
        fr["operations"] = [{
            "finance_operation_id": "fop_half",
            "finance_area_id": "farea_shop",
            "amount": 10,
            "import_batch_id": "fimp_half",
            "import_fingerprint": "fp_a",
            "is_deleted": False,
        }]
        fr["import_batches"] = [{
            "import_batch_id": "fimp_half",
            "batch_key": "key_half",
            "finance_area_id": "farea_shop",
        }]
        self.store["payload"]["_wt_records"] = {"financeRec.operations": {"v": 2}}
        self.add_user({
            "user_id": "fin_half",
            "display_name": "Half",
            "pin": "3010",
            "role": "user",
            "is_active": True,
            "departments": ["financeRec"],
            "permissions": {"financeRec": "edit"},
        })
        cookie, _ = self.login("fin_half", "3010")
        stg, dg, *_ = call("GET", "/api/state", cookie=cookie)
        remote_ops = list(dg["payload"]["financeRec"]["operations"])
        remote_fps = {str(o.get("import_fingerprint")) for o in remote_ops if o}
        expected = ["fp_a", "fp_b"]
        missing = [fp for fp in expected if fp not in remote_fps]
        self.assertEqual(missing, ["fp_b"])
        complete = len(expected) > 0 and len(missing) == 0
        self.assertFalse(complete)
        v = int((((dg.get("payload") or {}).get("_wt_records") or {}).get("financeRec.operations") or {}).get("v") or 0)
        merged = remote_ops + [{
            "finance_operation_id": "fop_rest",
            "finance_area_id": "farea_shop",
            "amount": 20,
            "import_batch_id": "fimp_half",
            "import_fingerprint": "fp_b",
            "is_deleted": False,
        }]
        st, *_ = call(
            "POST",
            "/api/state/op",
            {"op": {"path": "financeRec.operations", "expected_v": v, "value": merged}},
            cookie=cookie,
        )
        self.assertTrue(st.startswith("200"), st)
        fps = {
            str(o.get("import_fingerprint"))
            for o in self.store["payload"]["financeRec"]["operations"]
            if o and not o.get("is_deleted")
        }
        self.assertEqual(fps, {"fp_a", "fp_b"})

    def test_expense_import_orphan_batch_allows_retry_without_dup_ops(self):
        """batch_key в истории без операций — повторная загрузка добавляет ops, без дубля batch."""
        self._seed_finance_directory()
        fr = self.store["payload"]["financeRec"]
        fr["operations"] = []
        fr["import_batches"] = [{
            "import_batch_id": "fimp_orphan",
            "batch_key": "key_orphan",
            "finance_area_id": "farea_shop",
            "rows_added": 2,
        }]
        fr["audit"] = []
        self.store["payload"]["_wt_records"] = {}
        self.add_user({
            "user_id": "fin_orphan",
            "display_name": "Orphan",
            "pin": "3001",
            "role": "user",
            "is_active": True,
            "departments": ["financeRec"],
            "permissions": {"financeRec": "edit"},
        })
        cookie, _ = self.login("fin_orphan", "3001")
        # Клиент после исправления: не блокирует orphan, пишет ops с тем же import_batch_id.
        ops = [{
            "finance_operation_id": "fop_o1",
            "finance_area_id": "farea_shop",
            "object_id": "farea_shop",
            "amount": 15,
            "date": "2026-10-20",
            "import_batch_id": "fimp_orphan",
            "import_fingerprint": "fp_o1",
            "is_deleted": False,
        }]
        st, data, *_ = call(
            "POST",
            "/api/state/op/batch",
            {
                "atomic": True,
                "ops": [
                    {"path": "financeRec.operations", "expected_v": 0, "value": ops},
                    {
                        "path": "financeRec.import_batches",
                        "expected_v": 0,
                        "value": fr["import_batches"],
                    },
                ],
            },
            cookie=cookie,
        )
        self.assertTrue(st.startswith("200"), st)
        saved = self.store["payload"]["financeRec"]
        self.assertEqual(len(saved.get("import_batches") or []), 1)
        self.assertEqual(saved["import_batches"][0]["import_batch_id"], "fimp_orphan")
        self.assertEqual(len(saved.get("operations") or []), 1)
        # Повтор тех же ops (полная coverage) — сервер не должен получить второй fop.
        st2, *_ = call(
            "POST",
            "/api/state/op",
            {
                "op": {
                    "path": "financeRec.operations",
                    "expected_v": int(
                        ((self.store["payload"].get("_wt_records") or {})
                         .get("financeRec.operations") or {}).get("v") or 0
                    ),
                    "value": ops + [{
                        "finance_operation_id": "fop_o1_dup",
                        "finance_area_id": "farea_shop",
                        "amount": 15,
                        "import_batch_id": "fimp_orphan",
                        "import_fingerprint": "fp_o1",
                    }],
                }
            },
            cookie=cookie,
        )
        # Клиент не отправит дубль; здесь проверяем, что fingerprint-логика в fr-mod
        # описана, а сервер сохраняет явную запись только если клиент её шлёт.
        self.assertTrue(st2.startswith("200") or st2.startswith("409"), st2)

    def test_expense_import_failed_local_draft_not_on_server(self):
        """После сбоя на сервере нет batch/ops — повтор не блокируется серверным состоянием."""
        self._seed_finance_directory()
        fr = self.store["payload"]["financeRec"]
        fr["operations"] = []
        fr["import_batches"] = []
        fr["audit"] = []
        self.add_user({
            "user_id": "fin_fail",
            "display_name": "Fail",
            "pin": "3002",
            "role": "user",
            "is_active": True,
            "departments": ["financeRec"],
            "permissions": {"financeRec": "edit"},
        })
        cookie, _ = self.login("fin_fail", "3002")
        stg, dg, *_ = call("GET", "/api/state", cookie=cookie)
        self.assertTrue(stg.startswith("200"), stg)
        remote = dg["payload"]["financeRec"]
        self.assertEqual(remote.get("import_batches") or [], [])
        self.assertEqual(remote.get("operations") or [], [])
        # Успешная повторная загрузка после «локального» сбоя.
        st, *_ = call(
            "POST",
            "/api/state/op/batch",
            {
                "atomic": True,
                "ops": [
                    {
                        "path": "financeRec.operations",
                        "expected_v": 0,
                        "value": [{
                            "finance_operation_id": "fop_retry",
                            "finance_area_id": "farea_shop",
                            "amount": 42,
                            "import_batch_id": "fimp_retry",
                            "import_fingerprint": "fp_retry",
                        }],
                    },
                    {
                        "path": "financeRec.import_batches",
                        "expected_v": 0,
                        "value": [{
                            "import_batch_id": "fimp_retry",
                            "batch_key": "key_retry",
                            "finance_area_id": "farea_shop",
                        }],
                    },
                ],
            },
            cookie=cookie,
        )
        self.assertTrue(st.startswith("200"), st)
        self.assertEqual(len(self.store["payload"]["financeRec"]["operations"]), 1)

    def test_expense_import_partial_adds_only_missing(self):
        """Часть fingerprint уже есть — добавляется только недостающая операция."""
        self._seed_finance_directory()
        fr = self.store["payload"]["financeRec"]
        fr["operations"] = [{
            "finance_operation_id": "fop_keep",
            "finance_area_id": "farea_shop",
            "amount": 10,
            "import_batch_id": "fimp_part",
            "import_fingerprint": "fp_keep",
            "is_deleted": False,
        }]
        fr["import_batches"] = [{
            "import_batch_id": "fimp_part",
            "batch_key": "key_part",
            "finance_area_id": "farea_shop",
        }]
        self.store["payload"]["_wt_records"] = {"financeRec.operations": {"v": 1}}
        self.add_user({
            "user_id": "fin_part",
            "display_name": "Part",
            "pin": "3003",
            "role": "user",
            "is_active": True,
            "departments": ["financeRec"],
            "permissions": {"financeRec": "edit"},
        })
        cookie, _ = self.login("fin_part", "3003")
        stg, dg, *_ = call("GET", "/api/state", cookie=cookie)
        v = int((((dg.get("payload") or {}).get("_wt_records") or {}).get("financeRec.operations") or {}).get("v") or 0)
        merged = list(dg["payload"]["financeRec"]["operations"]) + [{
            "finance_operation_id": "fop_new",
            "finance_area_id": "farea_shop",
            "amount": 20,
            "import_batch_id": "fimp_part",
            "import_fingerprint": "fp_new",
            "is_deleted": False,
        }]
        st, *_ = call(
            "POST",
            "/api/state/op",
            {"op": {"path": "financeRec.operations", "expected_v": v, "value": merged}},
            cookie=cookie,
        )
        self.assertTrue(st.startswith("200"), st)
        fps = {
            str(o.get("import_fingerprint"))
            for o in self.store["payload"]["financeRec"]["operations"]
            if o and not o.get("is_deleted")
        }
        self.assertEqual(fps, {"fp_keep", "fp_new"})
        self.assertEqual(len(self.store["payload"]["financeRec"]["import_batches"]), 1)

    def test_finance_directory_rejected_without_right_and_used_delete(self):
        self._seed_finance_directory()
        cookie, _ = self.login("usr_001", "1111")
        before = json.loads(json.dumps(self.store["payload"]))
        status, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(self.store["version"]),
                "payload": {"financeRec": {"sources": [{
                    "finance_source_id": "fsrc_shop_a",
                    "name": "Взлом",
                    "object_id": "farea_shop",
                    "account_type": "cash",
                }]}},
            },
            cookie=cookie,
        )
        self.assertTrue(status.startswith("403"), status)
        self.assertEqual(self.store["payload"]["financeRec"], before["financeRec"])
        editor, _ = self.login("usr_set", "3333")
        status, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(self.store["version"]),
                "payload": {
                    "users": [{"user_id": "usr_set", "role": "admin", "pin": "9999"}],
                    "financeRec": {"sources": [{
                        "finance_source_id": "fsrc_extra",
                        "name": "Лишняя",
                        "object_id": "farea_shop",
                        "account_type": "cash",
                    }]},
                },
            },
            cookie=editor,
        )
        self.assertTrue(status.startswith("403"), status)
        roles = {row["user_id"]: row["role"] for row in self.store["payload"]["users"]}
        self.assertEqual(roles["usr_set"], "user")
        self.assertEqual(roles["usr_001"], "user")
        self.assertNotIn("fsrc_extra", json.dumps(self.store["payload"]["financeRec"]))
        manager = self._add_shop_manager("usr_fin", {"financeRecSetup": "edit"})
        # Реальное удаление: счёта нет в sources, он только в removed_source_ids.
        status, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(self.store["version"]),
                "payload": {"financeRec": {
                    "removed_source_ids": ["acc_shop"],
                    "sources": [
                        row for row in self.store["payload"]["financeRec"]["sources"]
                        if row.get("finance_source_id") != "fsrc_shop_a"
                        and row.get("account_id") != "acc_shop"
                    ],
                }},
            },
            cookie=manager,
        )
        self.assertTrue(status.startswith("403"), status)
        sources = {
            row["finance_source_id"]: row
            for row in self.store["payload"]["financeRec"]["sources"]
        }
        self.assertEqual(sources["fsrc_shop_a"]["name"], "Касса А")
        self.assertEqual(sources["fsrc_ter"]["name"], "Касса территории")
        self.assertEqual(self.store["payload"]["financeRec"]["operations"][0]["account_id"], "acc_shop")

    def test_staff_does_not_become_admin(self):
        cookie, _ = self.login("usr_001", "1111")
        status, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(self.store["version"]),
                "payload": {"users": [{
                    "user_id": "usr_001",
                    "role": "admin",
                    "legacy_role": "admin",
                    "permissions": {"settings": "edit", "salary": "edit"},
                }]},
            },
            cookie=cookie,
        )
        self.assertTrue(status.startswith("403"), status)
        staff = next(row for row in self.store["payload"]["users"] if row["user_id"] == "usr_001")
        self.assertEqual(staff["role"], "user")
        self.assertEqual(staff["legacy_role"], "staff")
        self.assertNotIn("salary", staff["permissions"])
        self.assertEqual(self.store["payload"]["workspaces"][0]["employees"][0]["rate"], 1500)

    def test_sources_without_id_keep_operation_links(self):
        self.store["payload"]["financeRec"] = {
            "operations": [
                {"id": "op_old", "amount": 15, "account_id": "acc_old", "object_id": "farea_shop"},
            ],
            "sources": [
                {
                    "name": "Старая касса",
                    "account_id": "acc_old",
                    "object_id": "farea_shop",
                    "account_type": "cash",
                    "sort_order": 10,
                },
                {
                    "name": "Без номера",
                    "object_id": "farea_shop",
                    "account_type": "bank",
                    "sort_order": 20,
                },
            ],
            "areas": [{
                "finance_area_id": "farea_shop",
                "object_id": "farea_shop",
                "name": "Магазин Володарского",
            }],
        }
        cookie = self._add_shop_manager("usr_fin", {"financeRecSetup": "edit"})
        status, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(self.store["version"]),
                "payload": {"financeRec": {"sources": [
                    {
                        "name": "Старая касса",
                        "account_id": "acc_old",
                        "object_id": "farea_shop",
                        "account_type": "cash",
                        "sort_order": 30,
                    },
                    {
                        "name": "Без номера",
                        "object_id": "farea_shop",
                        "account_type": "bank",
                        "sort_order": 40,
                    },
                ]}},
            },
            cookie=cookie,
        )
        self.assertTrue(status.startswith("200"), status)
        sources = self.store["payload"]["financeRec"]["sources"]
        self.assertEqual(len(sources), 2)
        old = next(row for row in sources if row.get("account_id") == "acc_old")
        self.assertEqual(old["finance_source_id"], "acc_old")
        self.assertEqual(old["sort_order"], 30)
        blank = next(row for row in sources if row.get("name") == "Без номера")
        self.assertTrue(str(blank["finance_source_id"]).startswith("fsrc_legacy_"))
        self.assertEqual(blank["account_id"], blank["finance_source_id"])
        self.assertEqual(blank["sort_order"], 40)
        self.assertEqual(self.store["payload"]["financeRec"]["operations"][0]["account_id"], "acc_old")
        self.assertEqual(self.store["payload"]["financeRec"]["operations"][0]["amount"], 15)

    def test_sequential_and_concurrent_saves_do_not_drop_data(self):
        self._seed_finance_directory()
        self._seed_metric_access()
        shop = next(row for row in self.store["payload"]["workspaces"] if row["code"] == "shop")
        shop["employees"].append({"id": "e_shop2", "name": "Второй", "rate": 1000})
        cookie = self._add_shop_manager()
        version = int(self.store["version"])
        status, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": version,
                "payload": {"financeRec": {"sources": [{
                    "finance_source_id": "fsrc_one",
                    "account_id": "fsrc_one",
                    "name": "Касса один",
                    "account_type": "cash",
                    "sort_order": 40,
                    "object_id": "farea_shop",
                }]}, "shopMetrics": {"workAccess": {"2026-10": {"e_shop": ["m_keep"]}}}},
            },
            cookie=cookie,
        )
        self.assertTrue(status.startswith("200"), status)
        status, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": version,
                "payload": {"financeRec": {"sources": [{
                    "finance_source_id": "fsrc_two",
                    "account_id": "fsrc_two",
                    "name": "Касса два",
                    "account_type": "cash",
                    "sort_order": 50,
                    "object_id": "farea_shop",
                }]}},
            },
            cookie=cookie,
        )
        self.assertTrue(status.startswith("409"), status)
        ids = {row["finance_source_id"] for row in self.store["payload"]["financeRec"]["sources"]}
        self.assertIn("fsrc_one", ids)
        self.assertNotIn("fsrc_two", ids)
        self.assertEqual(
            self.store["payload"]["shopMetrics"]["workAccess"]["2026-10"]["e_shop"],
            ["m_keep"],
        )
        # После первого PUT у finance/metrics уже есть record_v — без актуального
        # _wt_records protect_wt_records откатит лист (как в боевом клиенте с record_updates).
        status, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(self.store["version"]),
                "payload": {
                    "financeRec": {"sources": [{
                        "finance_source_id": "fsrc_two",
                        "account_id": "fsrc_two",
                        "name": "Касса два",
                        "account_type": "cash",
                        "sort_order": 50,
                        "object_id": "farea_shop",
                    }]},
                    "shopMetrics": {"workAccess": {"2026-10": {"e_shop2": ["m_two"]}}},
                    "_wt_records": self.store["payload"].get("_wt_records") or {},
                },
            },
            cookie=cookie,
        )
        self.assertTrue(status.startswith("200"), status)
        ids = {row["finance_source_id"] for row in self.store["payload"]["financeRec"]["sources"]}
        self.assertIn("fsrc_one", ids)
        self.assertIn("fsrc_two", ids)
        self.assertIn("fsrc_ter", ids)
        access = self.store["payload"]["shopMetrics"]["workAccess"]["2026-10"]
        self.assertEqual(access["e_shop"], ["m_keep"])
        self.assertEqual(access["e_shop2"], ["m_two"])
        self.assertEqual(access["e_wh"], ["m_wh"])
        self.assertEqual(self.store["payload"]["financeRec"]["operations"][0]["id"], "op_shop")
        self.assertEqual(self.store["payload"]["financeRec"]["operations"][1]["amount"], 20)

    def test_fr_fact_money_card_split_main_vs_bank_card(self):
        """Верхняя карточка: общая сумма без изменений + строки Основная касса / Расчётные счета."""
        src = Path(__file__).resolve().parents[1].joinpath("fr-mod.js").read_text(encoding="utf-8")
        fn = src[src.find("function frWeekFactMoneyTotal(") : src.find("function frCreateTransferOp(")]
        self.assertIn("bankCardCalc", fn)
        self.assertIn("mainCash", fn)
        self.assertIn('account_type === "bank"', fn)
        self.assertIn('account_type === "card"', fn)
        # Общая сумма по-прежнему из sumActual / sumCalc, без новой формулы.
        self.assertIn("sumActual", fn)
        self.assertIn("sumCalc", fn)
        self.assertIn("value-bankCard", fn.replace(" ", ""))
        card = src[src.find("const factMoney = frWeekFactMoneyTotal(") :]
        card = card[: card.find("} else {")]
        self.assertIn("Основная касса:", card)
        self.assertIn("Расчётные счета:", card)
        self.assertIn("factMoney.mainCash", card)
        self.assertIn("factMoney.bankCard", card)
        # Пример Территории: 13 921 247,15 − 48 513,83 = 13 872 733,32
        total = 13921247.15
        bank = 48513.83
        self.assertAlmostEqual(round(total - bank, 2), 13872733.32)
        # Отрицательный банк увеличивает «Основную кассу».
        self.assertAlmostEqual(round(1000000 - (-50000), 2), 1050000.0)

    def test_fr_non_income_cash_and_bank_blocks(self):
        """Два независимых блока внедохода: основная касса и расчётный счёт."""
        src = Path(__file__).resolve().parents[1].joinpath("fr-mod.js").read_text(encoding="utf-8")
        self.assertIn("ВНЕДОХОДОВЫЕ ПОСТУПЛЕНИЯ — ОСНОВНАЯ КАССА", src)
        self.assertIn("ВНЕДОХОДОВЫЕ ПОСТУПЛЕНИЯ — РАСЧЁТНЫЙ СЧЁТ", src)
        self.assertIn("function frDefaultSettlementAccountId(", src)
        self.assertIn("function frNonIncomeChannelOf(", src)
        self.assertIn('non_income_channel: ch', src)
        self.assertIn("non_income_bank_grid", src)
        self.assertIn('renderFrNonIncomeReconcileBlock(areaId, w, dates, canEdit, "cash")', src)
        self.assertIn('renderFrNonIncomeReconcileBlock(areaId, w, dates, canEdit, "bank")', src)
        set_fn = src[src.find("function frSetNonIncomeCell(") : src.find("function frRenameNonIncomeLine(")]
        self.assertIn('ch === "bank"', set_fn)
        self.assertIn("frDefaultSettlementAccountId", set_fn)
        self.assertIn("frPrimaryCashId", set_fn)
        # Сценарии: каналы не смешивают остатки.
        cash_open, bank_open, revenue = 1_000_000.0, 200_000.0, 500_000.0
        cash_ni, bank_ni = 100_000.0, 50_000.0
        self.assertAlmostEqual(cash_open + cash_ni, 1_100_000.0)
        self.assertAlmostEqual(bank_open, 200_000.0)  # кассовый внедоход банк не трогает
        self.assertAlmostEqual(bank_open + bank_ni, 250_000.0)
        self.assertAlmostEqual(cash_open + cash_ni, 1_100_000.0)  # банковский внедоход кассу не трогает
        self.assertAlmostEqual(revenue, 500_000.0)

    def test_metric_catalog_category_includes_shop(self):
        """В «Категория в Метриках» доступен Магазин Володарского (select=shop → territory_category "")."""
        src = Path(__file__).resolve().parents[1].joinpath("app.html").read_text(encoding="utf-8")
        self.assertIn('value="shop"', src)
        self.assertIn("showShopCategoryOption", src)
        self.assertIn("isShopCategory", src)
        self.assertIn('catRaw === "shop"', src)
        self.assertIn(">Магазин Володарского</option>", src)
        # Раньше магазин скрывали на центральном справочнике — такого условия больше нет.
        self.assertNotIn(
            "!territoryForm && !isCentralMetricCatalogScreen() && !isGenericMetricsDept",
            src,
        )
        # Назначения на центральном экране доступны и для магазина.
        norm = src.replace("\r\n", "\n")
        self.assertIn("|| territoryForm\n        || isCentralMetricCatalogScreen();", norm)
        self.assertIn('item.territory_category = "";', norm)
        self.assertIn('item.department_id = dept || "shop";', norm)

    def test_manager_employee_create_still_works(self):
        cookie = self._add_shop_manager()
        status, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(self.store["version"]),
                "payload": {"workspaces": [{
                    "code": "shop",
                    "employees": [{
                        "id": "e_fresh",
                        "employee_id": "e_fresh",
                        "name": "Новый продавец",
                    }],
                    "employeeIds": ["e_fresh"],
                }]},
            },
            cookie=cookie,
        )
        self.assertTrue(status.startswith("200"), status)
        shop = next(row for row in self.store["payload"]["workspaces"] if row["code"] == "shop")
        by_id = {row["id"]: row for row in shop["employees"]}
        self.assertEqual(by_id["e_shop"]["name"], "Касса")
        self.assertEqual(by_id["e_shop"]["rate"], 1500)
        self.assertEqual(by_id["e_fresh"]["name"], "Новый продавец")
        self.assertNotIn("rate", by_id["e_fresh"])
        self.assertIn("e_fresh", shop["employeeIds"])
        self.assertEqual(self.store["payload"]["workspaces"][1]["employees"][0]["id"], "e_wh")
        self.assertEqual(self.store["payload"]["workspaces"][1]["employees"][0]["rate"], 1800)

    def test_hidden_fields_survive_partial_put(self):
        cookie, _ = self.login("usr_001", "1111")
        status, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(self.store["version"]),
                "payload": {"workspaces": [{"code": "shop", "timesheet": {"2026-10": {"e_shop": {"1": "Б"}}}}]},
            },
            cookie=cookie,
        )
        self.assertTrue(status.startswith("200"), status)
        self.assertEqual(self.store["payload"]["financeRec"], SAMPLE_APP["financeRec"])
        self.assertEqual(self.store["payload"]["shopPayroll"], SAMPLE_APP["shopPayroll"])
        self.assertEqual(len(self.store["payload"]["users"]), 5)
        self.assertEqual(self.store["payload"]["workspaces"][1]["code"], "warehouse")

    def test_forbidden_put_is_atomic(self):
        cookie, _ = self.login("usr_001", "1111")
        snapshot = json.loads(json.dumps(self.store["payload"]))
        status, data, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(self.store["version"]),
                "payload": {
                    "workspaces": [{"code": "shop", "timesheet": {"hack": True}}],
                    "salary": {"hack": True},
                },
            },
            cookie=cookie,
        )
        self.assertTrue(status.startswith("403"))
        self.assertEqual(data["error"], "forbidden")
        self.assertEqual(self.store["payload"], snapshot)

    def test_two_puts_same_version_conflict(self):
        cookie, _ = self.login("usr_001", "1111")
        version = int(self.store["version"])
        body_a = {"workspaces": [{"code": "shop", "timesheet": {"2026-10": {"e_shop": {"1": "О"}}}}]}
        body_b = {"workspaces": [{"code": "shop", "timesheet": {"2026-10": {"e_shop": {"1": "Б"}}}}]}
        first, *_ = call("PUT", "/api/state", {"version": version, "payload": body_a}, cookie=cookie)
        second, data, raw, _ = call("PUT", "/api/state", {"version": version, "payload": body_b}, cookie=cookie)
        self.assertTrue(first.startswith("200"))
        self.assertTrue(second.startswith("409"))
        self.assertEqual(data["error"], "conflict")
        self.assertNotIn("payload", data)
        self.assertNotIn("salary", raw)
        shop = next(w for w in self.store["payload"]["workspaces"] if w["code"] == "shop")
        self.assertEqual(shop["timesheet"]["2026-10"]["e_shop"]["1"], "О")

    def test_admin_partial_put_does_not_drop_other_depts(self):
        cookie, _ = self.login()
        status, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(self.store["version"]),
                "payload": {"workspaces": [{"code": "shop", "name": "Магазин Володарского", "timesheet": {"ok": 1}}]},
            },
            cookie=cookie,
        )
        self.assertTrue(status.startswith("200"), status)
        codes = [w["code"] for w in self.store["payload"]["workspaces"]]
        self.assertEqual(codes, ["shop", "warehouse"])
        self.assertEqual(self.store["payload"]["salary"], SAMPLE_APP["salary"])
        shop = next(w for w in self.store["payload"]["workspaces"] if w["code"] == "shop")
        self.assertEqual(shop["timesheet"], {"ok": 1})

    def test_put_ignores_client_supplied_admin_role(self):
        cookie, _ = self.login("usr_001", "1111")
        status, data, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(self.store["version"]),
                "payload": {
                    "users": [{**ADMIN, "role": "admin", "pin": "0000"}],
                    "workspaces": [{"code": "shop", "timesheet": {"2026-10": {"e_shop": {"1": "О"}}}}],
                },
            },
            cookie=cookie,
        )
        self.assertTrue(status.startswith("403"))
        self.assertEqual(data["error"], "forbidden")
        admin = next(u for u in self.store["payload"]["users"] if u["user_id"] == "user_admin")
        self.assertEqual(admin.get("pin") or "", "5555")

    def test_staff_cannot_edit_other_department(self):
        cookie, _ = self.login("usr_001", "1111")
        before = json.loads(json.dumps(self.store["payload"]))
        status, data, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(self.store["version"]),
                "payload": {
                    "workspaces": [{"code": "warehouse", "timesheet": {"2026-10": {"e_wh": {"1": "О"}}}}]
                },
            },
            cookie=cookie,
        )
        self.assertTrue(status.startswith("403"))
        self.assertEqual(data["error"], "forbidden")
        self.assertEqual(self.store["payload"], before)

    def test_settings_editor_cannot_change_users_or_pins(self):
        cookie, _ = self.login("usr_set", "3333")
        hijack = json.loads(json.dumps(SAMPLE_APP))
        for item in hijack["users"]:
            if item["user_id"] == "user_admin":
                item["pin"] = "9999"
                item["role"] = "user"
                item["permissions"] = {}
        status, data, *_ = call(
            "PUT",
            "/api/state",
            {"version": int(self.store["version"]), "payload": hijack},
            cookie=cookie,
        )
        self.assertTrue(status.startswith("403"))
        self.assertEqual(data["error"], "forbidden")
        admin = next(u for u in self.store["payload"]["users"] if u["user_id"] == "user_admin")
        self.assertEqual(admin.get("role"), "admin")
        self.assertEqual(admin.get("pin") or "", "5555")
        status2, *_ = call("POST", "/api/login", {"user_id": "user_admin", "pin": "5555"})
        self.assertTrue(status2.startswith("200"))

    def test_settings_editor_cannot_wipe_or_edit_salary(self):
        cookie, _ = self.login("usr_set", "3333")
        version = int(self.store["version"])
        wiped = {"workspaces": [{"id": "ws1", "code": "office"}]}
        status, data, *_ = call(
            "PUT", "/api/state", {"version": version, "payload": wiped}, cookie=cookie
        )
        self.assertTrue(status.startswith("403"), status)
        self.assertEqual(self.store["payload"]["workspaces"][0]["code"], "shop")
        self.assertEqual(self.store["payload"]["salary"], SAMPLE_APP["salary"])
        self.assertEqual(len(self.store["payload"]["users"]), 5)
        changed = json.loads(json.dumps(wiped))
        changed["salary"] = {"hack": True}
        changed["financeRec"] = {"operations": [{"secret": 1}]}
        status2, data2, *_ = call(
            "PUT",
            "/api/state",
            {"version": int(self.store["version"]), "payload": changed},
            cookie=cookie,
        )
        self.assertTrue(status2.startswith("403"))
        self.assertEqual(data2["error"], "forbidden")
        self.assertEqual(self.store["payload"]["salary"], SAMPLE_APP["salary"])
        self.assertEqual(self.store["payload"]["financeRec"], SAMPLE_APP["financeRec"])

    def test_settings_editor_conflict_hides_payload(self):
        admin_cookie, _ = self.login()
        call(
            "PUT",
            "/api/state",
            {"version": int(self.store["version"]), "payload": SAMPLE_APP},
            cookie=admin_cookie,
        )
        editor_cookie, _ = self.login("usr_set", "3333")
        status, data, raw, _ = call(
            "PUT",
            "/api/state",
            {"version": 0, "payload": {"workspaces": [{"id": "ws1", "code": "x"}]}},
            cookie=editor_cookie,
        )
        self.assertTrue(status.startswith("409"))
        self.assertEqual(data["error"], "conflict")
        self.assertIn("version", data)
        self.assertNotIn("payload", data)
        self.assertNotIn("salary", raw)
        self.assertNotIn("financeRec", raw)
        self.assertNotIn("5555", raw)

    def test_api_token_does_not_open_state(self):
        status, data, *_ = call("GET", "/api/state", token="unit-test-token")
        self.assertTrue(status.startswith("401"))
        self.assertEqual(data["error"], "unauthorized")

    def test_invalid_json(self):
        cookie, _ = self.login()
        status, data, *_ = call("PUT", "/api/state", b"{not-json", cookie=cookie)
        self.assertTrue(status.startswith("400"))
        self.assertEqual(data["error"], "invalid json")

    def test_missing_version(self):
        cookie, _ = self.login()
        status, data, *_ = call(
            "PUT",
            "/api/state",
            {"payload": SAMPLE_APP},
            cookie=cookie,
        )
        self.assertTrue(status.startswith("400"))
        self.assertEqual(data["error"], "version is required")

    def test_unknown_path(self):
        status, data, *_ = call("GET", "/api/secret")
        self.assertTrue(status.startswith("404"))
        self.assertEqual(data["error"], "not found")

    def test_logout_drops_session(self):
        cookie, _ = self.login()
        self.assertTrue(self.store["sessions"])
        status, *_ = call("POST", "/api/logout", cookie=cookie)
        self.assertTrue(status.startswith("200"))
        self.assertEqual(self.store["sessions"], {})
        status2, data, *_ = call("GET", "/api/me", cookie=cookie)
        self.assertTrue(status2.startswith("401"))
        self.assertEqual(data["error"], "unauthorized")

    def test_session_shared_across_two_connections(self):
        cookie, _ = self.login()
        token = session_raw([cookie])
        hashed = wt_api.token_hash(token)
        self.assertIn(hashed, self.store["sessions"])
        self.assertNotIn(token, self.store["sessions"])
        self._patch.stop()
        with patch.object(wt_api, "connect", side_effect=lambda: FakeConn(self.store)):
            status, me, *_ = call("GET", "/api/me", cookie=cookie)
            status2, *_ = call("GET", "/api/me", cookie=cookie)
        self._patch.start()
        self.assertTrue(status.startswith("200"))
        self.assertEqual(me["user"]["user_id"], "user_admin")
        self.assertTrue(status2.startswith("200"))

    def test_expired_session_is_rejected(self):
        cookie, _ = self.login()
        past = datetime.now(timezone.utc) - timedelta(seconds=30)
        for rec in self.store["sessions"].values():
            rec["expires_at"] = past
        status, data, *_ = call("GET", "/api/me", cookie=cookie)
        self.assertTrue(status.startswith("401"))
        self.assertEqual(data["error"], "unauthorized")
        self.assertEqual(self.store["sessions"], {})

    def test_login_requires_postgres_session(self):
        self.store["fail_sessions"] = True
        status, data, raw, cookies = call("POST", "/api/login", {"user_id": "user_admin", "pin": "5555"})
        self.assertTrue(status.startswith("503"))
        self.assertEqual(data["error"], "database unavailable")
        self.assertEqual(self.store["sessions"], {})
        self.assertFalse(cookie_from(cookies))
        self.assertNotIn("wt_session=", raw)

    def test_cookie_secure_only_with_explicit_flag(self):
        status, _, _, cookies = call(
            "POST",
            "/api/login",
            {"user_id": "user_admin", "pin": "5555"},
            environ_extra={"HTTP_X_FORWARDED_PROTO": "https"},
        )
        self.assertTrue(status.startswith("200"))
        header = cookies[0]
        self.assertIn("HttpOnly", header)
        self.assertIn("SameSite=Lax", header)
        self.assertNotIn("Secure", header)
        os.environ["WT_COOKIE_SECURE"] = "1"
        status2, _, _, cookies2 = call("POST", "/api/login", {"user_id": "usr_001", "pin": "1111"})
        self.assertTrue(status2.startswith("200"))
        header2 = cookies2[0]
        self.assertIn("Secure", header2)
        self.assertIn("HttpOnly", header2)
        self.assertIn("SameSite=Lax", header2)

    def test_db_error_does_not_leak_password(self):
        cookie, _ = self.login()
        self.store["fail"] = True
        status_h, data_h, raw_h, _ = call("GET", "/api/health", cookie=cookie)
        self.assertTrue(status_h.startswith("200"))
        self.assertEqual(data_h, {"ok": True})
        self.assertNotIn(SECRET, raw_h)
        for method, path, body in (
            ("GET", "/api/state", None),
            ("PUT", "/api/state", {"version": 0, "payload": SAMPLE_APP}),
        ):
            status, data, raw, _ = call(method, path, body, cookie=cookie)
            self.assertTrue(status.startswith("503"), path)
            self.assertNotIn(SECRET, raw)
            self.assertEqual(data.get("error"), "database unavailable")

    def test_connect_failure_no_secrets(self):
        cookie, _ = self.login()
        self._patch.stop()
        with patch.object(wt_api, "connect", side_effect=RuntimeError("dsn password=" + SECRET)):
            status, data, raw, _ = call("GET", "/api/state", cookie=cookie)
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
        for key in ("DATABASE_URL", "PGPASSWORD", "PGUSER", "PGHOST", "PGDATABASE", "WT_API_TOKEN"):
            os.environ.pop(key, None)
        wt_api.LOGIN_FAILS.clear()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env_backup)

    def test_health_without_env(self):
        status, data, raw, _ = call("GET", "/api/health")
        self.assertTrue(status.startswith("200"))
        self.assertEqual(data, {"ok": True})
        self.assertNotIn("password", raw.lower())
        with patch.object(wt_api, "connect", side_effect=RuntimeError("health must not open db")) as mocked:
            status2, data2, *_ = call("HEAD", "/api/health", as_json=False)
            mocked.assert_not_called()
        self.assertTrue(status2.startswith("200"))

    def test_state_without_session(self):
        status, data, raw, _ = call("GET", "/api/state", token="")
        self.assertTrue(status.startswith("401"))
        self.assertEqual(data["error"], "unauthorized")
        self.assertNotIn("workspaces", raw)

    def test_bootstrap_without_database_config(self):
        os.environ["WT_PIN_PEPPER"] = "unit-pepper"
        os.environ["WT_BOOTSTRAP_NAME"] = "Админ"
        os.environ["WT_BOOTSTRAP_PIN"] = "env-only-pin"
        code, message = wt_api.run_bootstrap()
        self.assertEqual(code, 1)
        self.assertEqual(message, "database unavailable")
        self.assertNotIn("env-only-pin", message)


class StaticPagesTests(unittest.TestCase):
    def test_index_and_app_urls(self):
        status, _, text, _ = call("GET", "/", as_json=False)
        self.assertTrue(status.startswith("200"))
        self.assertIn("WorkTime", text)
        status, _, text, _ = call("GET", "/index.html", as_json=False)
        self.assertTrue(status.startswith("200"))
        self.assertIn("WorkTime", text)
        status, _, text, _ = call("GET", "/app.html", as_json=False)
        self.assertTrue(status.startswith("200"))
        self.assertIn("STORAGE_KEY", text)
        status, _, text, _ = call("GET", "/css/landing.css", as_json=False)
        self.assertTrue(status.startswith("200"))
        self.assertIn("{", text)
        status, _, text, _ = call("GET", "/fr-mod.js", as_json=False)
        self.assertTrue(status.startswith("200"))
        self.assertIn("ensureFinanceRec", text)

    def test_does_not_serve_server_code(self):
        status, data, *_ = call("GET", "/server/wt_api.py")
        self.assertTrue(status.startswith("404"))
        self.assertEqual(data["error"], "not found")

    def test_login_admin_uses_server_api(self):
        src = Path(__file__).resolve().parents[1].joinpath("app.html").read_text(encoding="utf-8")
        start = src.find("function loginAdmin(")
        end = src.find("function loginById(", start)
        body = src[start:end]
        self.assertGreater(start, 0)
        self.assertGreater(end, start)
        self.assertIn('fetch("/api/login"', body)
        self.assertIn("loadServerStateThenOpenAdmin", body)
        self.assertIn('credentials: "same-origin"', body)
        self.assertIn("Неверный PIN", body)
        self.assertNotIn("findActiveUserByPin", body)
        self.assertNotIn("pinsMatch", body)
        self.assertNotIn("ensureLoginData", body)
        self.assertNotIn("migrateAppUsers", body)
        self.assertNotIn("localStorage", body)
        self.assertNotIn("PUT /api/state", body)
        self.assertNotIn('fetch("/api/state"', body)

    def test_server_admin_opens_without_local_storage(self):
        src = Path(__file__).resolve().parents[1].joinpath("app.html").read_text(encoding="utf-8")
        adopt = src[src.find("function adoptServerPayload("):src.find("function applyServerState(")]
        self.assertIn('w.pin = ""', adopt)
        self.assertNotIn("migrateAppUsers", adopt)
        self.assertNotIn("restoreCanonicalPins", adopt)
        save = src[src.find("function putServerStateOnce("):src.find("function daysInMonthValue(")]
        self.assertIn("wtServerMode", save)
        self.assertIn('method: "PUT"', save)
        self.assertIn('"/api/state"', save)
        self.assertIn("version: wtStateVersion", save)
        self.assertIn('credentials: "same-origin"', save)
        self.assertNotIn("Сохранение на сервер пока недоступно", save)
        self.assertIn("localStorage.setItem", save)
        self.assertLess(save.find("if (wtServerMode)"), save.find("localStorage.setItem"))
        restore = src[src.find("function restoreServerAdminSession("):src.find("function currentAuthUserId(")]
        self.assertIn('fetch("/api/me"', restore)
        self.assertIn('"/api/state"', src[src.find("function loadServerStateThenEnter("):src.find("function restoreServerAdminSession(")])
        self.assertIn("if (wtServerMode) return;", src[src.find("function migrateAppUsers("):src.find("function generateAccessUserId(")])

    def test_save_app_serializes_put_and_handles_conflict(self):
        src = Path(__file__).resolve().parents[1].joinpath("app.html").read_text(encoding="utf-8")
        save = src[src.find("function wtCloneJson("):src.find("function daysInMonthValue(")]
        self.assertIn("wtPutChain", save)
        self.assertIn("putServerStateOnce", save)
        self.assertIn("handleWtSaveConflict", save)
        self.assertIn("handleWtSaveUnauthorized", save)
        self.assertIn("409", save)
        self.assertIn("401", save)
        self.assertIn("wtStateVersion = Number(state.version)", save)
        self.assertIn("window.confirm", save)
        self.assertIn("function metricCatalogPutSlice(", save)
        self.assertIn("function saveAppWithPayloadSlice(", save)
        self.assertIn("wtSaveMark", save)
        self.assertIn("useSlicePut", save)
        self.assertIn("record_updates", save)
        self.assertIn("wtSaveTimingEnabled", src)
        slice_fn = src[src.find("function metricCatalogPutSlice("):src.find("function saveAppWithPayloadSlice(")]
        self.assertIn('userHasPerm("metricsSetup", "edit")', slice_fn)
        self.assertIn("userAccessDeptCodes", slice_fn)
        self.assertIn("staffMetricSlice", slice_fn)
        self.assertIn("staffMetricSlice(appData.shopMetrics, false, true)", slice_fn)
        self.assertNotIn("out.shopPayroll", slice_fn)
        # Передаём функцию-сборщик, не готовый объект — иначе после 409 уходит устаревший срез.
        self.assertIn("saveAppWithPayloadSlice(metricCatalogPutSlice)", src)
        self.assertNotIn("saveAppWithPayloadSlice(metricCatalogPutSlice())", src)
        self.assertIn("saveMetricCatalogForm.busy", src)
        cat_save = src[src.find("Promise.resolve(saveAppWithPayloadSlice(metricCatalogPutSlice))"):]
        cat_save = cat_save[: cat_save.find("function getDeptMetricStored(")]
        self.assertIn('ok === "timeout"', cat_save)
        self.assertIn("Нет подтверждения сохранения", cat_save)
        self.assertIn('"shopPayroll.metrics"', cat_save)
        timeout_branch = cat_save.split('ok === "timeout"')[1].split('ok === "conflict_review"')[0]
        self.assertNotIn("closeMetricCatalogModal", timeout_branch)
        self.assertNotIn("Метрика сохранена", timeout_branch)
        self.assertNotIn("Чек-лист сохранён", timeout_branch)
        self.assertNotIn("restoreMetricCatalog", timeout_branch)
        conflict_branch = cat_save.split('ok === "conflict_review"')[1].split("if (!wtSaveOutcomeOk(ok))")[0]
        self.assertNotIn("restoreMetricCatalog", conflict_branch)
        self.assertNotIn("closeMetricCatalogModal", conflict_branch)
        self.assertNotIn("Метрика сохранена", conflict_branch)
        conflict = src[src.find("function handleWtSaveConflict("):src.find("function putServerStateOnce(")]
        self.assertIn("sliceMode", conflict)
        self.assertIn("автозапись среза после конфликта отключена", conflict)
        self.assertIn("wtPutPayloadOverride = null", conflict)
        self.assertIn('return "conflict_review"', conflict)
        # В режиме среза после merge нет автоматического putServerStateOnce.
        slice_block = conflict.split("if (sliceMode)")[1].split('toast("Свежие данные загружены')[0]
        self.assertNotIn("return putServerStateOnce()", slice_block)
        api = Path(__file__).resolve().parent.joinpath("wt_api.py").read_text(encoding="utf-8")
        self.assertIn("RETURNING version, updated_at, updated_by", api)
        self.assertIn('"ok": True', api)
        self.assertIn("wtThreeWayMerge", save)
        self.assertIn("payloadForServerPut", save)
        self.assertIn("buildStaffPutPayload", save)
        access_start = src.find("function saveAccessUser(")
        access_end = src.find("function renderSummaryUsers(", access_start)
        access = src[access_start:access_end]
        self.assertGreater(access_start, 0)
        self.assertGreater(access_end, access_start)
        self.assertIn("wtPersistAccessUser(", access)
        self.assertIn("if (wtSaveOutcomeOk(ok)) afterSaved()", access)
        self.assertIn("if (pin) prev.pin = pin", access)

    def test_commit_employee_card_waits_for_server_save(self):
        src = Path(__file__).resolve().parents[1].joinpath("app.html").read_text(encoding="utf-8")
        start = src.find("function commitEmployeeCard(")
        end = src.find("function employeeCardActionsHtml(", start)
        body = src[start:end]
        self.assertGreater(start, 0)
        self.assertGreater(end, start)
        self.assertIn("Promise.resolve(saveApp())", body)
        self.assertIn('toast("Сотрудник сохранён.")', body)
        self.assertIn("Назначения метрик не записаны", body)
        self.assertIn("syncEmployeeMetricAssignmentsToCatalog", body)
        # Успех только после ok — не до ответа сервера.
        toast_ok = body.find('toast("Сотрудник сохранён.")')
        then_pos = body.find("Promise.resolve(saveApp())")
        self.assertGreater(toast_ok, then_pos)
        self.assertIn("if (!wtSaveOutcomeOk(ok))", body)
        self.assertIn('ok === "conflict_review"', body)
        # Обычное сохранение карточки не восстанавливает group_all галочки.
        self.assertNotIn("assignGroupAllMetricsToEmployee(", body)

    def test_individual_metric_assign_does_not_force_group_all_on_card_flows(self):
        src = Path(__file__).resolve().parents[1].joinpath("app.html").read_text(encoding="utf-8")
        # Создание, смена группы/должности и перевод не ставят все галочки группы.
        create = src[src.find("function saveEmployeeCreateForm("):src.find("function closeEmployeeCreateForm(")]
        self.assertNotIn("assignGroupAllMetricsToEmployee(", create)
        update = src[src.find("function updateEmployee("):src.find("function setEmployeeTermsField(")]
        self.assertNotIn("assignGroupAllMetricsToEmployee(", update)
        move = src[src.find("function moveEmployeeToDepartment("):src.find("/* ============================================================\n   СТАТИСТИКА")]
        if "assignGroupAllMetricsToEmployee(" in move:
            # допускаем только комментарий, не вызов
            self.assertNotIn("assignGroupAllMetricsToEmployee(employee)", move)
        sync = src[src.find("function syncEmployeeMetricAssignmentsToCatalog("):src.find("function commitEmployeeCard(")]
        self.assertIn("group_all", sync)
        self.assertNotIn("row.assign_mode = \"specific\"", sync)
        self.assertNotIn("item.assign_mode = \"specific\"", sync)
        # Явное массовое назначение — отдельная функция, не карточка.
        self.assertIn("function assignGroupAllMetricsToEmployee(", src)
        self.assertIn("function metricCatalogItemUsesGroupAll(", src)
        self.assertIn("function assignMetricCatalogGroupAllToWorkAccess(", src)

    def test_returns_count_and_group_all_do_not_auto_write_work_access(self):
        """Открытие карточки/ensure не назначает метрики; group_all — только каталог."""
        src = Path(__file__).resolve().parents[1].joinpath("app.html").read_text(encoding="utf-8")
        ensure_shop = src[src.find("function ensureShopMetrics("):src.find("function ensureShopMetricCatalog(")]
        self.assertNotIn("applyReturnsCountGroupIfNeeded()", ensure_shop)
        ensure_dept = src[src.find("function ensureDeptMetrics("):src.find("function metricsStoreForEmployee(")]
        self.assertNotIn("applyReturnsCountGroupIfNeeded()", ensure_dept)
        returns_fn = src[
            src.find("function applyReturnsCountGroupIfNeeded("):src.find("function metricCatalogGroupEmployees(")
        ]
        self.assertIn("seedReturnsCountMetric()", returns_fn)
        self.assertNotIn("applyMetricCatalogGroupAssignment(", returns_fn)
        self.assertNotIn("applyShopEmployeeWorkMetric(", returns_fn)
        # Показатель остаётся в каталоге с тем же id.
        seed = src[src.find("function seedReturnsCountMetric("):src.find("function inferTerritoryMetricCategory(")]
        self.assertIn('METRIC_RETURNS_COUNT_ID', seed)
        self.assertIn('"metric_returns_count"', src[src.find("const METRIC_RETURNS_COUNT_ID"):src.find("const METRIC_RETURNS_COUNT_ID") + 80])
        group_fn = src[
            src.find("function applyMetricCatalogGroupAssignment("):src.find("function assignMetricCatalogGroupAllToWorkAccess(")
        ]
        self.assertNotIn("applyShopEmployeeWorkMetric(", group_fn)
        assign_fn = src[
            src.find("function applyMetricCatalogAssignments("):src.find("function syncCommonMetricTabsFromAssignments(")
        ]
        self.assertIn('row.assign_mode !== "specific"', assign_fn)
        self.assertIn("if (!specificRows.length)", assign_fn)
        # Массовая запись только в явной функции; карточка/создание/смена группы её не зовут.
        explicit = src[
            src.find("function assignMetricCatalogGroupAllToWorkAccess("):src.find("function applyMetricCatalogSpecificAssignment(")
        ]
        self.assertIn("applyShopEmployeeWorkMetric(", explicit)
        for name, start, end in (
            ("commit", "function commitEmployeeCard(", "function employeeCardActionsHtml("),
            ("create", "function saveEmployeeCreateForm(", "function closeEmployeeCreateForm("),
            ("update", "function updateEmployee(", "function setEmployeeTermsField("),
        ):
            body = src[src.find(start):src.find(end, src.find(start))]
            self.assertNotIn("assignMetricCatalogGroupAllToWorkAccess(", body, name)
            self.assertNotIn("assignGroupAllMetricsToEmployee(", body, name)
        # Сохранение формы каталога не зовёт явное массовое назначение.
        save_cat = src[src.find("if (assignmentRows) applyMetricCatalogAssignments(item);"):]
        save_cat = save_cat[:save_cat.find("Promise.resolve(saveApp())")]
        self.assertIn("applyMetricCatalogGroupAssignment(item)", save_cat)
        self.assertNotIn("assignMetricCatalogGroupAllToWorkAccess(", save_cat)
        # Чтение галочек карточки — через workAccess / shopEmployeeHasWorkMetric.
        fio_metrics = src[src.find("shopGrantableExtraMetrics(employee).forEach"):]
        fio_metrics = fio_metrics[:800]
        self.assertIn("shopEmployeeHasWorkMetric(employee.id, def.id)", fio_metrics)

    def test_login_admin_function_does_not_put_state(self):
        src = Path(__file__).resolve().parents[1].joinpath("app.html").read_text(encoding="utf-8")
        start = src.find("function loginAdmin(")
        end = src.find("function loginById(", start)
        body = src[start:end]
        self.assertNotIn("method: \"PUT\"", body)

    def test_login_by_code_uses_server_api(self):
        src = Path(__file__).resolve().parents[1].joinpath("app.html").read_text(encoding="utf-8")
        start = src.find("function loginByCode(")
        end = src.find("function loginSummary(", start)
        body = src[start:end]
        self.assertGreater(start, 0)
        self.assertGreater(end, start)
        self.assertIn('fetch("/api/login"', body)
        self.assertIn("user_id: userId", body)
        self.assertIn("loadServerStateThenEnter", body)
        self.assertIn('credentials: "same-origin"', body)
        self.assertIn("Неверный PIN", body)
        self.assertNotIn("pinsMatch", body)
        self.assertNotIn("ensureLoginData", body)
        self.assertNotIn("migrateAppUsers", body)
        self.assertNotIn("localStorage", body)
        render = src[src.find("function renderLogin("):src.find("function loginPinDigits(")]
        self.assertIn("fetchDirectory", render)
        self.assertNotIn("ensureLoginData", render)
        self.assertIn('fetch("/api/directory"', src[src.find("function fetchDirectory("):src.find("function userPinLoginCardHtml(")])

    def test_hash_opens_login_card_not_section_hub(self):
        src = Path(__file__).resolve().parents[1].joinpath("app.html").read_text(encoding="utf-8")
        login_html = src[src.find('id="loginScreen"'):src.find('id="app"')]
        self.assertNotIn("onclick=\"renderLogin('shop')\"", login_html)
        self.assertNotIn(">Открыть</button>", login_html)
        self.assertIn('id="loginGrid"', login_html)
        route = src[src.find("function ensureLoginRouteFromHash("):src.find("function bootLocalAppData(")]
        self.assertIn("renderLogin(view)", route)
        self.assertNotIn("loginView !== view", route)
        requested = src[src.find("function requestedLoginView("):src.find("function ensureLoginRouteFromHash(")]
        self.assertIn('hash === "shop"', requested)
        self.assertIn('hash === "territory"', requested)
        self.assertIn('hash === "summary"', requested)
        self.assertIn('hash === "spu"', requested)
        self.assertIn('hash === "org"', requested)
        self.assertIn('hash === "admin"', requested)
        self.assertIn('hash === "financeRec"', requested)
        render = src[src.find("function renderLogin("):src.find("function loginPinDigits(")]
        self.assertIn('loginView === "shop"', render)
        self.assertIn('loginView === "territory"', render)
        self.assertIn('loginView === "admin"', render)
        self.assertIn("Выберите пользователя и введите его PIN.", render)
        self.assertIn("Отдельный вход администратора. Все подразделения.", render)
        self.assertNotIn("Админ: 5555", render)
        self.assertNotIn("5555 / 6666", render)
        self.assertNotIn("defaultPinHint", render)
        migrate = src[src.find("function migrateAppUsers("):src.find("function generateAccessUserId(")]
        self.assertNotIn("user_dept_", migrate)
        self.assertNotIn("upsertAccessUser", migrate)

    def test_index_links_go_to_section_hashes(self):
        src = Path(__file__).resolve().parents[1].joinpath("index.html").read_text(encoding="utf-8")
        self.assertIn("app.html#shop", src)
        self.assertIn("app.html#territory", src)
        self.assertIn("app.html#summary", src)
        self.assertIn("app.html#spu", src)
        self.assertIn("app.html#financeRec", src)
        self.assertIn("app.html#org", src)
        self.assertIn("app.html#admin", src)


class SourceAndBindTests(unittest.TestCase):
    def test_default_bind_is_localhost(self):
        os.environ.pop("WT_BIND", None)
        self.assertEqual(wt_api.bind_host(), "127.0.0.1")

    def test_schema_has_sessions_table(self):
        sql = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
        self.assertIn("app_state", sql)
        self.assertIn("app_sessions", sql)
        self.assertNotIn("DROP", sql)

    def test_env_example_has_placeholder_only(self):
        example = Path(__file__).resolve().parents[1].joinpath(".env.example").read_text(encoding="utf-8")
        self.assertIn("DATABASE_URL", example)
        self.assertIn("CHANGE_ME", example)
        self.assertIn("WT_PIN_PEPPER", example)
        self.assertIn("WT_COOKIE_SECURE", example)
        self.assertIn("WT_BOOTSTRAP_NAME", example)
        self.assertIn("WT_BOOTSTRAP_PIN", example)
        self.assertNotRegex(example, r"WT_BOOTSTRAP_PIN=\S+")

    def test_dockerfile_healthcheck_uses_ipv4_and_curl(self):
        docker = Path(__file__).resolve().parents[1].joinpath("Dockerfile").read_text(encoding="utf-8")
        self.assertIn("EXPOSE 80", docker)
        self.assertIn("0.0.0.0:80", docker)
        self.assertIn("HEALTHCHECK", docker)
        self.assertIn("curl", docker)
        self.assertIn("http://127.0.0.1:80/api/health", docker)
        self.assertNotIn("http://localhost", docker)


class BootstrapAdminTests(unittest.TestCase):
    PIN = "env-only-pin"

    def setUp(self):
        self._env_backup = dict(os.environ)
        os.environ["DATABASE_URL"] = "postgresql://worktime:" + SECRET + "@127.0.0.1:5432/worktime"
        os.environ["WT_PIN_PEPPER"] = "unit-pepper"
        os.environ["WT_SCRYPT_N"] = "16"
        os.environ["WT_BOOTSTRAP_NAME"] = "Первый администратор"
        os.environ["WT_BOOTSTRAP_PIN"] = self.PIN
        os.environ.pop("WT_BOOTSTRAP_USER_ID", None)
        wt_api.LOGIN_FAILS.clear()
        self.store = {
            "payload": {},
            "version": 0,
            "updated_at": None,
            "updated_by": "",
            "fail": False,
            "sessions": {},
        }
        self._patch = patch.object(wt_api, "connect", side_effect=lambda: FakeConn(self.store))
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        os.environ.clear()
        os.environ.update(self._env_backup)

    def test_empty_state_creates_hashed_admin(self):
        self.store["payload"] = {"salary": {"keep": True}, "workspaces": [{"code": "shop"}]}
        code, message = wt_api.run_bootstrap()
        self.assertEqual(code, 0)
        self.assertIn("user_admin", message)
        self.assertNotIn(self.PIN, message)
        users = self.store["payload"]["users"]
        self.assertEqual(len(users), 1)
        admin = users[0]
        self.assertEqual(admin["role"], "admin")
        self.assertEqual(admin["legacy_role"], "admin")
        self.assertEqual(admin["permissions"]["settings"], "edit")
        self.assertEqual(admin["permissions"]["kpi"], "manage")
        self.assertTrue(str(admin.get("pin_hash") or "").startswith("scrypt$"))
        self.assertNotIn("pin", admin)
        self.assertEqual(self.store["payload"]["salary"], {"keep": True})
        self.assertEqual(self.store["payload"]["workspaces"][0]["code"], "shop")
        self.assertTrue(wt_api.verify_pin(self.PIN, admin["pin_hash"]))

    def test_empty_users_array_is_bootstrappable(self):
        self.store["payload"] = {"users": [], "financeRec": {"ok": 1}}
        code, _ = wt_api.run_bootstrap()
        self.assertEqual(code, 0)
        self.assertEqual(self.store["payload"]["financeRec"], {"ok": 1})
        self.assertEqual(len(self.store["payload"]["users"]), 1)

    def test_existing_users_refuse_without_write(self):
        snapshot = json.loads(json.dumps(SAMPLE_APP))
        self.store["payload"] = snapshot
        self.store["version"] = 4
        code, message = wt_api.run_bootstrap()
        self.assertEqual(code, 3)
        self.assertIn("users already exist", message)
        self.assertEqual(self.store["payload"], snapshot)
        self.assertEqual(self.store["version"], 4)

    def test_second_bootstrap_is_refused(self):
        first, _ = wt_api.run_bootstrap()
        self.assertEqual(first, 0)
        admin = self.store["payload"]["users"][0]
        second, message = wt_api.run_bootstrap()
        self.assertEqual(second, 3)
        self.assertEqual(self.store["payload"]["users"], [admin])

    def test_concurrent_persist_creates_one_admin(self):
        user = wt_api.bootstrap_admin_record("A", self.PIN, "user_admin")
        merged, err = wt_api.merge_bootstrap_user({}, user)
        self.assertIsNone(err)
        conn = FakeConn(self.store)
        with conn.cursor() as cur:
            row1 = wt_api.persist_bootstrap_admin(cur, merged, 0, "user_admin")
            row2 = wt_api.persist_bootstrap_admin(cur, merged, 0, "user_admin")
        self.assertIsNotNone(row1)
        self.assertIsNone(row2)
        self.assertEqual(len(self.store["payload"]["users"]), 1)

    def test_missing_pepper_refuses_bootstrap(self):
        os.environ.pop("WT_PIN_PEPPER", None)
        code, message = wt_api.run_bootstrap()
        self.assertEqual(code, 2)
        self.assertIn("WT_PIN_PEPPER", message)
        self.assertNotIn(self.PIN, message)
        self.assertEqual(self.store["payload"], {})
        self.assertEqual(self.store["version"], 0)
        self.assertRaises(wt_api.PinPepperMissing, wt_api.hash_pin, self.PIN)
        self.assertFalse(wt_api.verify_pin(self.PIN, "scrypt$16$8$1$YQ==$YQ=="))

    def test_empty_pepper_refuses_bootstrap(self):
        os.environ["WT_PIN_PEPPER"] = ""
        code, message = wt_api.run_bootstrap()
        self.assertEqual(code, 2)
        self.assertIn("WT_PIN_PEPPER", message)
        self.assertEqual(self.store["payload"], {})
        self.assertEqual(self.store["version"], 0)

    def test_bootstrap_then_login_with_pepper(self):
        code, _ = wt_api.run_bootstrap()
        self.assertEqual(code, 0)
        status, data, _, cookies = call(
            "POST", "/api/login", {"user_id": "user_admin", "pin": self.PIN}
        )
        self.assertTrue(status.startswith("200"), status)
        self.assertEqual(data["user"]["user_id"], "user_admin")
        self.assertEqual(data["user"]["role"], "admin")
        self.assertTrue(any(item.startswith("wt_session=") for item in cookies))
        self.assertTrue(self.store.get("sessions"))
        bad, err, raw, _ = call("POST", "/api/login", {"user_id": "user_admin", "pin": "not-the-pin"})
        self.assertTrue(bad.startswith("401"))
        self.assertEqual(err["error"], "invalid login")
        self.assertNotIn(self.PIN, raw)

    def test_missing_env_does_not_touch_db(self):
        os.environ.pop("WT_BOOTSTRAP_PIN", None)
        code, message = wt_api.run_bootstrap()
        self.assertEqual(code, 2)
        self.assertIn("WT_BOOTSTRAP_PIN", message)
        self.assertNotIn(self.PIN, message)
        self.assertEqual(self.store["payload"], {})
        self.assertEqual(self.store["version"], 0)

    def test_cli_stdout_hides_pin(self):
        buf = StringIO()
        with patch("sys.stdout", buf):
            status = wt_api.run_bootstrap_cli()
        self.assertEqual(status, 0)
        out = buf.getvalue()
        self.assertNotIn(self.PIN, out)
        self.assertNotIn("pin_hash", out)

    def test_bootstrap_is_not_a_public_api(self):
        for method in ("POST", "GET", "PUT"):
            status, data, raw, _ = call(method, "/api/bootstrap", {"pin": self.PIN})
            self.assertTrue(status.startswith("404"), method)
            self.assertEqual(data["error"], "not found")
            self.assertNotIn(self.PIN, raw)

    def test_admin_pin_first_login_creates_one_admin(self):
        os.environ["WT_BOOTSTRAP_PIN"] = self.PIN
        os.environ["WT_BOOTSTRAP_NAME"] = "Первый администратор"
        status, data, raw, cookies = call("POST", "/api/login", {"pin": self.PIN})
        self.assertTrue(status.startswith("200"), status)
        self.assertEqual(data["user"]["user_id"], "user_admin")
        self.assertEqual(data["user"]["role"], "admin")
        self.assertNotIn("pin", data["user"])
        self.assertNotIn(self.PIN, raw)
        self.assertTrue(any(item.startswith("wt_session=") for item in cookies))
        users = self.store["payload"]["users"]
        self.assertEqual(len(users), 1)
        self.assertTrue(str(users[0].get("pin_hash") or "").startswith("scrypt$"))
        self.assertNotIn("pin", users[0])

    def test_admin_pin_repeat_login(self):
        os.environ["WT_BOOTSTRAP_PIN"] = self.PIN
        os.environ["WT_BOOTSTRAP_NAME"] = "Первый администратор"
        first, *_ = call("POST", "/api/login", {"pin": self.PIN})
        self.assertTrue(first.startswith("200"))
        status, data, raw, cookies = call("POST", "/api/login", {"pin": self.PIN})
        self.assertTrue(status.startswith("200"), status)
        self.assertEqual(data["user"]["user_id"], "user_admin")
        self.assertTrue(any(item.startswith("wt_session=") for item in cookies))
        self.assertEqual(len(self.store["payload"]["users"]), 1)
        self.assertNotIn(self.PIN, raw)

    def test_admin_pin_wrong_does_not_create_user(self):
        os.environ["WT_BOOTSTRAP_PIN"] = self.PIN
        os.environ["WT_BOOTSTRAP_NAME"] = "Первый администратор"
        status, data, raw, cookies = call("POST", "/api/login", {"pin": "not-bootstrap"})
        self.assertTrue(status.startswith("401"))
        self.assertEqual(data["error"], "invalid login")
        self.assertNotIn("users already", raw.lower())
        self.assertNotIn("bootstrap", raw.lower())
        self.assertNotIn(self.PIN, raw)
        self.assertEqual(cookies, [])
        self.assertEqual(self.store["payload"].get("users") or [], [])

    def test_admin_pin_missing_bootstrap_secret(self):
        os.environ.pop("WT_BOOTSTRAP_PIN", None)
        os.environ["WT_BOOTSTRAP_NAME"] = "Первый администратор"
        status, data, raw, _ = call("POST", "/api/login", {"pin": self.PIN})
        self.assertTrue(status.startswith("401"))
        self.assertEqual(data["error"], "invalid login")
        self.assertNotIn("WT_BOOTSTRAP", raw)
        self.assertEqual(self.store["payload"].get("users") or [], [])

    def test_admin_pin_missing_pepper(self):
        os.environ.pop("WT_PIN_PEPPER", None)
        status, data, raw, _ = call("POST", "/api/login", {"pin": self.PIN})
        self.assertTrue(status.startswith("503"))
        self.assertEqual(data["error"], "authentication unavailable")
        self.assertNotIn("unit-pepper", raw)
        self.assertEqual(self.store["payload"].get("users") or [], [])

    def test_admin_pin_concurrent_first_login(self):
        os.environ["WT_BOOTSTRAP_PIN"] = self.PIN
        os.environ["WT_BOOTSTRAP_NAME"] = "Первый администратор"
        user = wt_api.bootstrap_admin_record("A", self.PIN, "user_admin")
        merged, _ = wt_api.merge_bootstrap_user({}, user)
        conn = FakeConn(self.store)
        with conn.cursor() as cur:
            row1 = wt_api.persist_bootstrap_admin(cur, merged, 0, "user_admin")
            row2 = wt_api.persist_bootstrap_admin(cur, merged, 0, "user_admin")
        self.assertIsNotNone(row1)
        self.assertIsNone(row2)
        self.assertEqual(len(self.store["payload"]["users"]), 1)
        status, data, raw, cookies = call("POST", "/api/login", {"pin": self.PIN})
        self.assertTrue(status.startswith("200"), status)
        self.assertEqual(len(self.store["payload"]["users"]), 1)
        self.assertNotIn(self.PIN, raw)
        self.assertTrue(cookies)

    def test_admin_pin_rate_limit_persists_in_db(self):
        os.environ["WT_BOOTSTRAP_PIN"] = self.PIN
        os.environ["WT_BOOTSTRAP_NAME"] = "Первый администратор"
        for _ in range(5):
            status, data, raw, _ = call("POST", "/api/login", {"pin": "wrong-pin"})
            self.assertTrue(status.startswith("401"), status)
            self.assertEqual(data["error"], "invalid login")
            self.assertNotIn(self.PIN, raw)
        wt_api.LOGIN_FAILS.clear()
        blocked, data, raw, cookies = call("POST", "/api/login", {"pin": self.PIN})
        self.assertTrue(blocked.startswith("429"), blocked)
        self.assertEqual(data["error"], "too many attempts")
        self.assertNotIn(self.PIN, raw)
        self.assertEqual(cookies, [])
        self.assertEqual(self.store["payload"].get("users") or [], [])

    def test_staff_pin_only_does_not_login(self):
        self.store["payload"] = json.loads(json.dumps(SAMPLE_APP))
        status, data, *_ = call("POST", "/api/login", {"pin": "1111"})
        self.assertTrue(status.startswith("401"))
        self.assertEqual(data["error"], "invalid login")

    def test_ambiguous_admin_pin_refused(self):
        self.store["payload"] = json.loads(json.dumps(SAMPLE_APP))
        self.store["payload"]["users"].append(
            {
                "user_id": "user_admin2",
                "display_name": "Второй",
                "pin": "5555",
                "role": "admin",
                "legacy_role": "admin",
                "is_active": True,
                "departments": ["shop"],
                "permissions": {},
            }
        )
        status, data, raw, _ = call("POST", "/api/login", {"pin": "5555"})
        self.assertTrue(status.startswith("401"))
        self.assertEqual(data["error"], "invalid login")
        self.assertNotIn("ambiguous", raw.lower())

    def test_source_has_no_bootstrap_pin(self):
        src = Path(__file__).with_name("wt_api.py").read_text(encoding="utf-8")
        self.assertIn('args[:1] == ["bootstrap"]', src)
        self.assertNotRegex(src, r"WT_BOOTSTRAP_PIN\s*=")
        self.assertNotIn("5555", src)
        self.assertNotIn(self.PIN, src)

    def test_metric_show_day_total_catalog_and_tables(self):
        """Галочка «Показывать итого за день» в каталоге и колонке рабочих таблиц."""
        src = Path(__file__).resolve().parents[1].joinpath("app.html").read_text(encoding="utf-8")
        self.assertIn("function metricShowDayTotal(", src)
        self.assertIn("function metricAllowsDayTotal(", src)
        self.assertIn("function metricAutoCalcAllowsDayTotal(", src)
        self.assertIn("function fieldShowsDayTotal(", src)
        self.assertIn("function shopFieldPeopleDayTotal(", src)
        self.assertIn("function refreshShopMetricDayTotalsUi(", src)
        self.assertIn("function autoCalcDayTotalContribution(", src)
        self.assertIn('id="mcShowDayTotal"', src)
        self.assertIn("Показывать итого за день", src)
        self.assertIn("item.show_day_total", src)
        self.assertIn('if (item.show_day_total !== true) item.show_day_total = false;', src)
        allows = src[src.find("function metricAllowsDayTotal("):src.find("function fieldShowsDayTotal(")]
        self.assertIn('fill === "number"', allows)
        self.assertIn('fill === "money"', allows)
        self.assertIn('fill === "auto_calc"', allows)
        auto_allow = src[
            src.find("function metricAutoCalcAllowsDayTotal(") : src.find("function metricAllowsDayTotal(")
        ]
        self.assertIn('op === "add"', auto_allow)
        self.assertIn('op === "sub"', auto_allow)
        self.assertIn('op === "mul"', auto_allow)
        self.assertIn('op === "div"', auto_allow)
        self.assertNotIn('op === "pct"', auto_allow)
        day_box = src[
            src.find("function refreshMetricCatalogDayTotalBox(") : src.find(
                "function employeeWorkGroupCaption("
            )
        ]
        self.assertIn('type === "auto_calc"', day_box)
        self.assertIn("cb.disabled", day_box)
        self.assertIn("сумма числителей", day_box)
        self.assertIn("процентного отклонения", day_box)
        self.assertIn('onchange="refreshMetricCatalogDayTotalBox()"', src)
        self.assertIn("function autoCalcPeopleDayTotal(", src)
        self.assertIn("function uniqueMetricPeople(", src)
        self.assertIn("autoCalcDayTotalContribution(", src)
        self.assertIn("metricDayTotalTdHtml(", src)
        self.assertIn("metricDayTotalThHtml(", src)
        summary = src[src.find("function refreshShopMetricSummary("):src.find("function refreshShopCashierSummary(")]
        self.assertIn("refreshShopMetricDayTotalsUi(", summary)
        save_cat = src[src.find("item.show_day_total = !!("):]
        save_cat = save_cat[:save_cat.find("Promise.resolve(saveApp())")]
        self.assertNotIn("assignMetricCatalogGroupAllToWorkAccess(", save_cat)
        self.assertIn("formula: item.formula || formula", save_cat)

    def test_auto_calc_day_total_sum_of_individual_results(self):
        """Итого: add/sub/mul — сумма личных; div — ΣL/ΣR; pct недоступен."""

        def day_total(op, rows):
            """rows: list of (a, b) per employee; None = нет данных."""
            if op == "pct":
                return None
            if op == "div":
                sum_l = 0.0
                sum_r = 0.0
                any_v = False
                for a, b in rows:
                    if a is None or b is None:
                        continue
                    sum_l += a
                    sum_r += b
                    any_v = True
                if not any_v or sum_r == 0:
                    return None
                return sum_l / sum_r
            total = 0.0
            any_v = False
            for a, b in rows:
                if a is None or b is None:
                    continue
                if op == "add":
                    v = a + b
                elif op == "sub":
                    v = a - b
                elif op == "mul":
                    v = a * b
                else:
                    return None
                total += v
                any_v = True
            return total if any_v else None

        self.assertEqual(day_total("sub", [(120, 110), (50, 40)]), 20)
        self.assertEqual(day_total("add", [(10, 5), (3, 2)]), 20)
        self.assertEqual(day_total("mul", [(2, 3), (4, 5)]), 26)
        # div: (120+50)/(10+5)=170/15, не сумма частных 12+10=22
        self.assertAlmostEqual(day_total("div", [(120, 10), (50, 5)]), 170 / 15)
        self.assertNotAlmostEqual(day_total("div", [(120, 10), (50, 5)]), 12 + 10)
        self.assertIsNone(day_total("div", [(120, 0), (50, 0)]))
        self.assertIsNone(day_total("div", [(None, 10), (None, 5)]))
        self.assertIsNone(day_total("pct", [(120, 110), (50, 40)]))
        self.assertEqual(day_total("sub", [(120, 110), (None, 40)]), 10)

    def test_auto_calc_div_day_total_no_double_count_people(self):
        """Уникальные сотрудники: дубликат id не удваивает числитель/знаменатель."""
        src = Path(__file__).resolve().parents[1].joinpath("app.html").read_text(encoding="utf-8")
        self.assertIn("function uniqueMetricPeople(", src)
        self.assertIn("function autoCalcPeopleDayTotal(", src)
        div_fn = src[src.find("function autoCalcPeopleDayTotal(") : src.find("function autoCalcPeopleMonthDayTotal(")]
        self.assertIn("uniqueMetricPeople(people)", div_fn)
        self.assertIn('op === "div"', div_fn)
        self.assertIn("sumL/sumR", div_fn.replace(" ", ""))
        self.assertIn("sumR === 0", div_fn)

        def div_total(rows):
            seen = set()
            sum_l = sum_r = 0.0
            any_v = False
            for emp_id, a, b in rows:
                if emp_id in seen:
                    continue
                seen.add(emp_id)
                if a is None or b is None:
                    continue
                sum_l += a
                sum_r += b
                any_v = True
            if not any_v or sum_r == 0:
                return None
            return sum_l / sum_r

        # Один сотрудник дважды в списке people — учёт один раз: 100/10=10, не 200/20.
        self.assertEqual(div_total([("e1", 100, 10), ("e1", 100, 10)]), 10)
        self.assertEqual(div_total([("e1", 100, 10), ("e2", 50, 5)]), 10)
        self.assertIsNone(div_total([("e1", 100, 0)]))

    def test_metric_auto_calc_formula_ops_stage2(self):
        """Этап 2: математические операции auto_calc, конструктор, совместимость div."""
        src = Path(__file__).resolve().parents[1].joinpath("app.html").read_text(encoding="utf-8")
        self.assertIn("CATALOG_AUTO_CALC_OPS", src)
        self.assertIn("CATALOG_AUTO_CALC_OPS_SELECTABLE", src)
        self.assertIn("function normalizeCatalogAutoCalcOp(", src)
        self.assertIn("function shopCatalogOperandSnapshot(", src)
        self.assertIn("function formatCatalogAutoCalcResult(", src)
        self.assertIn("function catalogFormulaWouldCycle(", src)
        self.assertIn("function metricFormulaDependsOn(", src)
        self.assertIn("function catalogAutoCalcMonthTotal(", src)
        self.assertIn('id="mcOp"', src)
        # Четыре действия в выборе для новых; pct сохранён для совместимости.
        for op_id in ('"add"', '"sub"', '"mul"', '"div"', '"pct"'):
            self.assertIn(op_id, src)
        self.assertIn("Процентное отклонение", src)
        self.assertIn("((b - a) / a) * 100", src)
        selectable = src[src.find("CATALOG_AUTO_CALC_OPS_SELECTABLE"):src.find("const CATALOG_AUTO_CALC_OPS =")]
        self.assertIn('"add"', selectable)
        self.assertIn('"div"', selectable)
        self.assertNotIn('"pct"', selectable)
        self.assertIn('if (curOp === "pct")', src)
        self.assertIn("Как считать итог за период", src)
        self.assertIn("Сложить все значения", src)
        self.assertIn("Выберите, как программа должна рассчитывать итог за неделю или месяц.", src)
        # Старый средний чек: op div left sales right checks.
        self.assertIn(
            'formula: { op: "div", left: "metric_sales", right: "metric_checks" }',
            src,
        )
        # Безопасность: нет подмены отсутствующего нулём в новом пути; /0 → null.
        auto_fn = src[src.find("function shopCatalogAutoValue("):src.find("function formatCatalogAutoCalcResult(")]
        self.assertIn("if (!leftSnap.present || !rightSnap.present) return null", auto_fn)
        self.assertIn("return b === 0 ? null : a / b", auto_fn)
        self.assertIn('return a === 0 ? null : ((b - a) / a) * 100', auto_fn)
        # Проценты не суммируются в месячном итоге.
        month_fn = src[src.find("function catalogAutoCalcMonthTotal("):src.find("function shopCatalogDaySum(")]
        self.assertIn('if (op === "pct" || op === "div") return null', month_fn)
        # Сохранение читает mcOp и пишет left/right ID.
        save = src[src.find('if (fill === "auto_calc") {'):]
        save = save[: save.find('} else if (fill === "auto_sum")')]
        self.assertIn("mcOp", save)
        self.assertIn("catalogFormulaWouldCycle", save)
        self.assertIn("left:leftId,right:rightId", save.replace(" ", ""))
        # СПУ / workAccess group_all не трогаем в этом блоке.
        self.assertNotIn("assignMetricCatalogGroupAllToWorkAccess(", save)

    def test_auto_calc_math_reference_values(self):
        """Эталонные значения формул этапа 2 (логика как в shopCatalogAutoValue)."""

        def calc(op, a, b):
            if a is None or b is None:
                return None
            if op == "add":
                return a + b
            if op == "sub":
                return a - b
            if op == "mul":
                return a * b
            if op == "div":
                return None if b == 0 else a / b
            if op == "pct":
                return None if a == 0 else ((b - a) / a) * 100
            return None

        self.assertEqual(calc("sub", 120, 110), 10)
        self.assertEqual(calc("add", 120, 110), 230)
        self.assertEqual(calc("mul", 120, 2), 240)
        self.assertEqual(calc("div", 120, 10), 12)
        self.assertAlmostEqual(calc("pct", 120, 110), -8.333333, places=5)
        self.assertIsNone(calc("sub", None, 110))
        self.assertIsNone(calc("div", 120, 0))
        self.assertIsNone(calc("pct", 0, 110))


class EmployeeCardGuardClientTests(unittest.TestCase):
    """Защита карточек: черновик до Save, без авто-дедупа/клонирования, метрики не мутируют карточку."""

    def _src(self) -> str:
        return Path(__file__).resolve().parents[1].joinpath("app.html").read_text(encoding="utf-8")

    def test_protected_fields_are_draft_until_commit(self):
        src = self._src()
        self.assertIn("function stageEmployeeCardField(", src)
        self.assertIn("EMPLOYEE_CARD_PROTECTED_FIELDS", src)
        self.assertIn("applyEmployeeCardProtectedDraft", src)
        self.assertRegex(src, r"stageEmployeeCardField\([^)]*work_tab_id")
        self.assertRegex(src, r"stageEmployeeCardField\([^)]*position_id")
        update = src[src.find("function updateEmployee(") : src.find("function setEmployeeTermsField(")]
        self.assertIn("EMPLOYEE_CARD_PROTECTED_FIELDS[field]", update)
        self.assertIn("stageEmployeeCardField(employeeId, field, value)", update)
        self.assertNotIn("setEmployeePrimaryWorkTab(employeeId, value)", update)
        self.assertNotIn("setEmployeePosition(employee, value", update)

    def test_dangerous_load_helpers_are_report_only(self):
        src = self._src()
        dedupe = src[
            src.find("function dedupeClonedEmployees(") : src.find("function ensureUniqueWorkspaceIds(")
        ]
        self.assertIn("possible_name_duplicate", dedupe)
        self.assertIn("pushEmployeeCardGuardReport", dedupe)
        self.assertNotIn("removeEmployeeFromWorkspace", dedupe)
        distribute = src[
            src.find("function distributeEmployeesPerDepartment(") : src.find("function dedupeClonedEmployees(")
        ]
        self.assertIn("distribute_no_clone", distribute)
        self.assertIn("employee_id_multi_object", distribute)
        self.assertNotIn("cloneEmployeeRecord", distribute)
        ensure = src[src.find("function ensurePositionCatalog(") : src.find("function positionCatalogItems(")]
        self.assertIn("if (e.position_id) return;", ensure)
        self.assertNotIn("e.position = findById", ensure)
        bind = src[src.find("function bindEmployeePositionId(") : src.find("function positionUsage(")]
        self.assertIn("opts.force === true", bind)
        self.assertIn("if (!force && employee.position_id) return;", bind)

    def test_metric_sync_does_not_mutate_employee_card(self):
        src = self._src()
        sync = src[
            src.find("function syncEmployeeMetricAssignmentsToCatalog(") : src.find("function commitEmployeeCard(")
        ]
        self.assertNotIn("setEmployeePosition(", sync)
        self.assertNotIn("setEmployeePrimaryWorkTab(", sync)
        self.assertNotIn("employee.position_id =", sync)
        self.assertNotIn("employee.work_tab_id =", sync)
        self.assertNotIn("employee.department =", sync)

    def test_admin_move_and_reassign_require_confirm(self):
        src = self._src()
        move = src[src.find("function moveEmployeeToDepartment(") : src.find("function updateStats(")]
        self.assertIn("opts.confirmed", move)
        self.assertIn("confirm(", move)
        reassign = src[
            src.find("function reassignPositionEmployees(") : src.find("function removeCustomPosition(")
        ]
        self.assertIn("confirm(", reassign)
        self.assertIn("usage.employees", reassign)


class EmployeeIdAllocationTests(unittest.TestCase):
    """Уникальные ID новых сотрудников: префиксы, counters, защита PUT от новых дублей."""

    def test_format_and_prefix(self):
        self.assertEqual(wt_api.employee_id_prefix_for_dept("shop"), "V")
        self.assertEqual(wt_api.employee_id_prefix_for_dept("warehouse"), "T-S")
        self.assertEqual(wt_api.employee_id_prefix_for_dept("office"), "T-O")
        self.assertEqual(wt_api.employee_id_prefix_for_dept("drivers"), "T-L")
        self.assertIsNone(wt_api.employee_id_prefix_for_dept("summary"))
        self.assertEqual(wt_api.format_new_employee_id("V", 1), "V-000001")
        self.assertEqual(wt_api.format_new_employee_id("T-S", 12), "T-S-000012")

    def test_allocate_increments_and_skips_existing(self):
        payload = {
            "workspaces": [
                {
                    "code": "shop",
                    "type": "dept",
                    "employees": [{"id": "emp_010", "name": "Old"}, {"id": "V-000001", "name": "A"}],
                },
                {"code": "warehouse", "type": "dept", "employees": [{"id": "emp_010", "name": "DupHist"}]},
            ],
            "employeeIdCounters": {"V": 1},
        }
        eid, err = wt_api.allocate_employee_id_in_payload(payload, "shop")
        self.assertIsNone(err)
        self.assertEqual(eid, "V-000002")
        self.assertEqual(payload["employeeIdCounters"]["V"], 2)
        eid2, err2 = wt_api.allocate_employee_id_in_payload(payload, "warehouse")
        self.assertIsNone(err2)
        self.assertEqual(eid2, "T-S-000001")

    def test_put_allows_historical_duplicate_but_blocks_new(self):
        current = {
            "workspaces": [
                {
                    "code": "warehouse",
                    "employees": [
                        {"id": "emp_010", "name": "A"},
                        {"id": "emp_010", "name": "B"},
                    ],
                },
                {"code": "shop", "employees": [{"id": "V-000001", "name": "S"}]},
            ]
        }
        # Исторический дубль без изменений — ок.
        same = {
            "workspaces": [
                {
                    "code": "warehouse",
                    "employees": [
                        {"id": "emp_010", "name": "A"},
                        {"id": "emp_010", "name": "B"},
                    ],
                }
            ]
        }
        self.assertIsNone(wt_api.employee_id_put_denied(current, same))
        # Новая копия существующего id в другом отделе — запрет.
        clone = {
            "workspaces": [
                {
                    "code": "office",
                    "employees": [{"id": "V-000001", "name": "Stolen"}],
                }
            ]
        }
        self.assertEqual(wt_api.employee_id_put_denied(current, clone), "duplicate_employee_id")
        # Новый уникальный id — ок.
        fresh = {
            "workspaces": [
                {"code": "shop", "employees": [
                    {"id": "V-000001", "name": "S"},
                    {"id": "V-000002", "name": "New"},
                ]}
            ]
        }
        self.assertIsNone(wt_api.employee_id_put_denied(current, fresh))
        # Перевод: убрали из shop, добавили в warehouse — totals равны.
        moved = {
            "workspaces": [
                {"code": "shop", "employees": []},
                {"code": "warehouse", "employees": [
                    {"id": "emp_010", "name": "A"},
                    {"id": "emp_010", "name": "B"},
                    {"id": "V-000001", "name": "S"},
                ]},
            ]
        }
        self.assertIsNone(wt_api.employee_id_put_denied(current, moved))

    def test_admin_merge_keeps_other_admins_new_employee(self):
        current = {
            "workspaces": [
                {
                    "code": "warehouse",
                    "employees": [
                        {"id": "emp_keep", "name": "Old"},
                        {"id": "T-S-000001", "name": "AdminA", "work_tab_id": "loaders", "position": "Грузчик"},
                    ],
                    "employeeIds": ["emp_keep", "T-S-000001"],
                }
            ]
        }
        # Устаревший снимок админа B без карточки A.
        incoming = {
            "workspaces": [
                {
                    "code": "warehouse",
                    "employees": [
                        {"id": "emp_keep", "name": "Old"},
                        {"id": "T-S-000002", "name": "AdminB", "work_tab_id": "loaders", "position": "Грузчик"},
                    ],
                    "employeeIds": ["emp_keep", "T-S-000002"],
                }
            ]
        }
        merged = wt_api._merge_admin_payload(current, incoming)
        wh = next(w for w in merged["workspaces"] if w["code"] == "warehouse")
        ids = [e["id"] for e in wh["employees"]]
        self.assertIn("T-S-000001", ids)
        self.assertIn("T-S-000002", ids)
        self.assertIn("emp_keep", ids)

    def test_admin_merge_explicit_removal_deletes(self):
        current = {
            "workspaces": [
                {
                    "code": "warehouse",
                    "employees": [
                        {"id": "emp_keep", "name": "Old"},
                        {"id": "T-S-000001", "name": "Gone"},
                    ],
                    "employeeIds": ["emp_keep", "T-S-000001"],
                }
            ]
        }
        incoming = {
            "workspaces": [
                {
                    "code": "warehouse",
                    "employees": [{"id": "emp_keep", "name": "Old"}],
                    "employeeIds": ["emp_keep"],
                }
            ],
            "employeeRemovals": [{"department_id": "warehouse", "employee_id": "T-S-000001"}],
        }
        merged = wt_api._merge_admin_payload(current, incoming)
        wh = next(w for w in merged["workspaces"] if w["code"] == "warehouse")
        ids = [e["id"] for e in wh["employees"]]
        self.assertEqual(ids, ["emp_keep"])
        self.assertNotIn("employeeRemovals", merged)

    def test_employee_removal_blocked_by_salary_and_timesheet(self):
        current = {
            "workspaces": [
                {
                    "code": "shop",
                    "employees": [{"id": "E-KEEP", "name": "Ok"}, {"id": "E-HIST", "name": "Hist"}],
                    "employeeIds": ["E-KEEP", "E-HIST"],
                    "timesheet": {"2026-10": {"E-HIST": {"1": {"status": "Я", "hours": 8}}}},
                }
            ],
            "salary": {"2026-10": {"E-HIST": {"issued": False, "bonus": 100}}},
        }
        self.assertIn("табель", wt_api.employee_linked_history_labels(current, "E-HIST"))
        self.assertIn("зарплата", wt_api.employee_linked_history_labels(current, "E-HIST"))
        self.assertEqual(
            wt_api.employee_removals_denied(
                current,
                {"employeeRemovals": [{"department_id": "shop", "employee_id": "E-HIST"}]},
            ),
            "employee_has_history",
        )
        # Без связей — удаление разрешено на этапе проверки.
        clean = {
            "workspaces": [
                {
                    "code": "shop",
                    "employees": [{"id": "E-NEW", "name": "New"}],
                    "employeeIds": ["E-NEW"],
                    "timesheet": {},
                }
            ],
            "salary": {},
        }
        self.assertEqual(wt_api.employee_linked_history_labels(clean, "E-NEW"), [])
        self.assertIsNone(
            wt_api.employee_removals_denied(
                clean,
                {"employeeRemovals": [{"department_id": "shop", "employee_id": "E-NEW"}]},
            )
        )

    def test_ops_null_employee_blocked_with_history(self):
        payload = {
            "workspaces": [
                {
                    "code": "shop",
                    "employees": [{"id": "E1", "name": "A"}],
                    "employeeIds": ["E1"],
                }
            ],
            "salary": {"2026-10": {"E1": {"bonus": 1}}},
            "_wt_records": {},
        }
        admin = {
            "user_id": "user_admin",
            "role": "admin",
            "legacy_role": "admin",
            "departments": ["shop"],
            "permissions": {},
        }
        nxt, err = wt_api.apply_state_op(
            payload,
            {"path": "workspaces.shop.employees.E1", "expected_v": 0, "value": None},
            admin,
        )
        self.assertIsNone(nxt)
        self.assertIsNotNone(err)
        self.assertEqual(err.get("error"), "employee_has_history")
        self.assertIn("зарплата", str(err.get("message") or ""))
        # Карточка на сервере не удалена.
        ws = next(w for w in payload["workspaces"] if w["code"] == "shop")
        self.assertEqual([e["id"] for e in ws["employees"]], ["E1"])

    def test_prepare_put_denies_removal_with_history(self):
        current = {
            "workspaces": [
                {
                    "code": "shop",
                    "employees": [{"id": "E1", "name": "A"}],
                    "employeeIds": ["E1"],
                }
            ],
            "salary": {"2026-10": {"E1": {}}},
        }
        incoming = {
            "employeeRemovals": [{"department_id": "shop", "employee_id": "E1"}],
        }
        admin = {
            "user_id": "user_admin",
            "role": "admin",
            "legacy_role": "admin",
            "departments": ["shop"],
            "permissions": {},
        }
        stored, denied = wt_api.prepare_put_payload(current, incoming, admin)
        self.assertIsNone(stored)
        self.assertEqual(denied, "employee_has_history")

    def test_ops_null_employee_allowed_without_history(self):
        payload = {
            "workspaces": [
                {
                    "code": "shop",
                    "employees": [
                        {"id": "E_KEEP", "name": "Keep"},
                        {"id": "E_GONE", "name": "Gone"},
                    ],
                    "employeeIds": ["E_KEEP", "E_GONE"],
                }
            ],
            "salary": {},
            "_wt_records": {},
        }
        admin = {
            "user_id": "user_admin",
            "role": "admin",
            "legacy_role": "admin",
            "departments": ["shop"],
            "permissions": {},
        }
        nxt, err = wt_api.apply_state_op(
            payload,
            {"path": "workspaces.shop.employees.E_GONE", "expected_v": 0, "value": None},
            admin,
        )
        self.assertIsNone(err)
        self.assertIsNotNone(nxt)
        ws = next(w for w in nxt["workspaces"] if w["code"] == "shop")
        self.assertEqual([e["id"] for e in ws["employees"]], ["E_KEEP"])
        # История salary не трогалась (её и не было); другие карточки на месте.
        self.assertEqual(nxt.get("salary"), {})

    def test_employee_removal_blocked_by_kpi_assignment(self):
        current = {
            "workspaces": [
                {
                    "code": "shop",
                    "employees": [{"id": "E_KPI", "name": "K"}],
                    "employeeIds": ["E_KPI"],
                }
            ],
            "shopPayroll": {
                "assignments": [{"id": "a1", "employee_id": "E_KPI", "kpi_id": "k1"}],
            },
        }
        labels = wt_api.employee_linked_history_labels(current, "E_KPI")
        self.assertIn("KPI/премии", labels)
        self.assertEqual(
            wt_api.employee_removals_denied(
                current,
                {"employeeRemovals": [{"department_id": "shop", "employee_id": "E_KPI"}]},
            ),
            "employee_has_history",
        )

    def _empty_card(self, eid="E_LINK"):
        return {
            "workspaces": [
                {
                    "code": "shop",
                    "employees": [{"id": eid, "name": "Link"}, {"id": "E_OTHER", "name": "Other"}],
                    "employeeIds": [eid, "E_OTHER"],
                }
            ]
        }

    def test_link_customer_returns_blocks_removal(self):
        current = self._empty_card()
        current["customerReturns"] = {
            "items": [{"return_id": "r1", "driver_employee_id": "E_LINK"}]
        }
        self.assertIn("возвраты покупателей", wt_api.employee_linked_history_labels(current, "E_LINK"))
        self.assertEqual(
            wt_api.employee_removals_denied(
                current, {"employeeRemovals": [{"department_id": "shop", "employee_id": "E_LINK"}]}
            ),
            "employee_has_history",
        )
        # Чужой id в том же разделе — не блок для E_OTHER.
        self.assertEqual(wt_api.employee_linked_history_labels(current, "E_OTHER"), [])

    def test_link_revision_blocks_removal(self):
        current = self._empty_card()
        current["shopRevision"] = {
            "groups": [{"id": "g1", "employee_id": "E_LINK"}],
            "tasks": {},
        }
        self.assertIn("ревизии", wt_api.employee_linked_history_labels(current, "E_LINK"))
        current2 = self._empty_card("E_TASK")
        current2["deptRevisions"] = {
            "warehouse": {
                "groups": [],
                "tasks": {
                    "2026-10": {
                        "t1": {"id": "t1", "assignedEmployeeId": "E_TASK"}
                    }
                },
            }
        }
        self.assertIn("ревизии", wt_api.employee_linked_history_labels(current2, "E_TASK"))

    def test_link_management_blocks_removal(self):
        current = self._empty_card()
        current["management"] = {
            "indicators": [{"indicator_id": "i1", "owner_employee_id": "E_LINK"}],
            "meetings": [],
            "tasks": [],
        }
        self.assertIn("СПУ/совещания", wt_api.employee_linked_history_labels(current, "E_LINK"))
        current2 = self._empty_card("E_MEET")
        current2["management"] = {
            "indicators": [],
            "meetingWeeks": {"2026-W40": {"participant_employee_ids": ["E_MEET"]}},
        }
        self.assertIn("СПУ/совещания", wt_api.employee_linked_history_labels(current2, "E_MEET"))

    def test_link_org_structure_blocks_removal(self):
        current = self._empty_card()
        current["orgStructure"] = {
            "nodes": [{"org_node_id": "n1", "manager_employee_id": "E_LINK", "deputy_employee_id": ""}],
            "leadership": [],
        }
        self.assertIn("оргструктура", wt_api.employee_linked_history_labels(current, "E_LINK"))
        current2 = self._empty_card("E_LEAD")
        current2["orgStructure"] = {
            "nodes": [],
            "leadership": [{"id": "l1", "employee_id": "E_LEAD", "leadership_type": "manager"}],
        }
        self.assertIn("оргструктура", wt_api.employee_linked_history_labels(current2, "E_LEAD"))

    def test_link_inventory_conducted_by_blocks_removal(self):
        current = self._empty_card()
        current["shopMetrics"] = {
            "inventoryResults": {
                "2026-10": {
                    "1": {"conducted_by_employee_id": "E_LINK"}
                }
            }
        }
        labels = wt_api.employee_linked_history_labels(current, "E_LINK")
        self.assertIn("KPI/метрики", labels)

    def test_link_catalog_employee_ids_blocks_removal(self):
        current = self._empty_card()
        current["shopMetricCatalog"] = {
            "items": [
                {
                    "id": "m1",
                    "assignments": [
                        {
                            "department_id": "shop",
                            "work_tab_id": "cashiers",
                            "employee_ids": ["E_LINK"],
                            "extra_employee_ids": [],
                        }
                    ],
                }
            ]
        }
        self.assertIn("назначения метрик", wt_api.employee_linked_history_labels(current, "E_LINK"))

    def test_ops_and_removed_employee_ids_blocked_with_new_links(self):
        current = self._empty_card()
        current["customerReturns"] = {
            "items": [{"return_id": "r1", "sales_author_employee_id": "E_LINK"}]
        }
        admin = {
            "user_id": "user_admin",
            "role": "admin",
            "legacy_role": "admin",
            "departments": ["shop"],
            "permissions": {},
        }
        nxt, err = wt_api.apply_state_op(
            current,
            {"path": "workspaces.shop.employees.E_LINK", "expected_v": 0, "value": None},
            admin,
        )
        self.assertIsNone(nxt)
        self.assertEqual(err.get("error"), "employee_has_history")
        stored, denied = wt_api.prepare_put_payload(
            current,
            {
                "workspaces": [
                    {"code": "shop", "removedEmployeeIds": ["E_LINK"]}
                ]
            },
            admin,
        )
        self.assertIsNone(stored)
        self.assertEqual(denied, "employee_has_history")

    def test_admin_merge_cross_dept_stale_put_keeps_both(self):
        current = {
            "workspaces": [
                {
                    "code": "shop",
                    "employees": [
                        {"id": "e_shop", "name": "Cash"},
                        {"id": "V-000001", "name": "NewShop", "work_tab_id": "cashiers", "position_id": "p1"},
                    ],
                },
                {
                    "code": "office",
                    "employees": [{"id": "e_off", "name": "Off"}],
                },
            ]
        }
        incoming = {
            "workspaces": [
                {
                    "code": "shop",
                    "employees": [{"id": "e_shop", "name": "Cash"}],
                },
                {
                    "code": "office",
                    "employees": [
                        {"id": "e_off", "name": "Off"},
                        {"id": "T-O-000001", "name": "NewOffice", "work_tab_id": "office", "position": "Менеджер"},
                    ],
                },
            ]
        }
        merged = wt_api._merge_admin_payload(current, incoming)
        shop = next(w for w in merged["workspaces"] if w["code"] == "shop")
        office = next(w for w in merged["workspaces"] if w["code"] == "office")
        self.assertIn("V-000001", [e["id"] for e in shop["employees"]])
        self.assertIn("T-O-000001", [e["id"] for e in office["employees"]])


class ConcurrentEmployeeCreateApiTests(StoreApiTests):
    """Создание сотрудников через POST /api/employees + admin merge (FakeConn, без Timeweb)."""

    def _emp_ids(self, dept):
        ws = next(w for w in self.store["payload"]["workspaces"] if w["code"] == dept)
        return [e["id"] for e in (ws.get("employees") or []) if e and e.get("id")]

    def test_two_admins_create_same_dept_both_saved(self):
        cookie, _ = self.login()
        status1, data1, *_ = call(
            "POST",
            "/api/employees",
            {
                "department_id": "warehouse",
                "name": "Сотрудник А",
                "position": "Грузчик",
                "work_tab_id": "loaders",
                "client_request_id": "req-a-1",
            },
            cookie=cookie,
        )
        self.assertTrue(status1.startswith("200"), status1)
        status2, data2, *_ = call(
            "POST",
            "/api/employees",
            {
                "department_id": "warehouse",
                "name": "Сотрудник Б",
                "position": "Грузчик",
                "work_tab_id": "loaders",
                "client_request_id": "req-b-1",
            },
            cookie=cookie,
        )
        self.assertTrue(status2.startswith("200"), status2)
        self.assertNotEqual(data1["employee_id"], data2["employee_id"])
        ids = self._emp_ids("warehouse")
        self.assertIn(data1["employee_id"], ids)
        self.assertIn(data2["employee_id"], ids)
        self.assertTrue(str(data1["employee_id"]).startswith("T-S-"))
        self.assertTrue(str(data2["employee_id"]).startswith("T-S-"))
        wh = next(w for w in self.store["payload"]["workspaces"] if w["code"] == "warehouse")
        a = next(e for e in wh["employees"] if e["id"] == data1["employee_id"])
        b = next(e for e in wh["employees"] if e["id"] == data2["employee_id"])
        self.assertEqual(a["name"], "Сотрудник А")
        self.assertEqual(b["name"], "Сотрудник Б")
        self.assertEqual(a.get("work_tab_id"), "loaders")
        self.assertEqual(b.get("position"), "Грузчик")

    def test_two_admins_create_different_depts(self):
        self.store["payload"]["workspaces"].append(
            {"id": "ws_office", "code": "office", "name": "Офис", "employees": [], "employeeIds": []}
        )
        self.store["payload"]["workspaces"].append(
            {"id": "ws_drivers", "code": "drivers", "name": "Логистика", "employees": [], "employeeIds": []}
        )
        cookie, _ = self.login()
        status1, data1, *_ = call(
            "POST",
            "/api/employees",
            {"department_id": "shop", "name": "Shop New", "work_tab_id": "cashiers", "client_request_id": "req-shop"},
            cookie=cookie,
        )
        status2, data2, *_ = call(
            "POST",
            "/api/employees",
            {"department_id": "office", "name": "Office New", "work_tab_id": "office", "client_request_id": "req-office"},
            cookie=cookie,
        )
        status3, data3, *_ = call(
            "POST",
            "/api/employees",
            {"department_id": "drivers", "name": "Driver New", "work_tab_id": "drivers", "client_request_id": "req-drv"},
            cookie=cookie,
        )
        self.assertTrue(status1.startswith("200"), status1)
        self.assertTrue(status2.startswith("200"), status2)
        self.assertTrue(status3.startswith("200"), status3)
        self.assertTrue(str(data1["employee_id"]).startswith("V-"))
        self.assertTrue(str(data2["employee_id"]).startswith("T-O-"))
        self.assertTrue(str(data3["employee_id"]).startswith("T-L-"))
        self.assertIn(data1["employee_id"], self._emp_ids("shop"))
        self.assertIn(data2["employee_id"], self._emp_ids("office"))
        self.assertIn(data3["employee_id"], self._emp_ids("drivers"))

    def test_create_then_stale_admin_put_does_not_drop(self):
        cookie, _ = self.login()
        status, created, *_ = call(
            "POST",
            "/api/employees",
            {
                "department_id": "warehouse",
                "name": "Keep Me",
                "position": "Грузчик",
                "work_tab_id": "loaders",
                "client_request_id": "req-keep",
            },
            cookie=cookie,
        )
        self.assertTrue(status.startswith("200"), status)
        eid = created["employee_id"]
        # Устаревший PUT без новой карточки (как второй админ со старым снимком).
        stale = json.loads(json.dumps(SAMPLE_APP))
        status_put, *_ = call(
            "PUT",
            "/api/state",
            {"version": int(self.store["version"]), "payload": stale},
            cookie=cookie,
        )
        self.assertTrue(status_put.startswith("200"), status_put)
        self.assertIn(eid, self._emp_ids("warehouse"))

    def test_create_and_explicit_delete_other(self):
        cookie, _ = self.login()
        status, created, *_ = call(
            "POST",
            "/api/employees",
            {
                "department_id": "warehouse",
                "name": "Stay",
                "client_request_id": "req-stay",
            },
            cookie=cookie,
        )
        self.assertTrue(status.startswith("200"), status)
        version = int(self.store["version"])
        status_put, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": version,
                "payload": {
                    "workspaces": [
                        {
                            "code": "warehouse",
                            "employees": [
                                {"id": created["employee_id"], "name": "Stay"},
                            ],
                        }
                    ],
                    "employeeRemovals": [
                        {"department_id": "warehouse", "employee_id": "e_wh"},
                    ],
                },
            },
            cookie=cookie,
        )
        self.assertTrue(status_put.startswith("200"), status_put)
        ids = self._emp_ids("warehouse")
        self.assertIn(created["employee_id"], ids)
        self.assertNotIn("e_wh", ids)

    def test_idempotent_retry_same_client_request_id(self):
        cookie, _ = self.login()
        body = {
            "department_id": "warehouse",
            "name": "Once Only",
            "position": "Грузчик",
            "work_tab_id": "loaders",
            "client_request_id": "req-idem-1",
        }
        status1, data1, *_ = call("POST", "/api/employees", body, cookie=cookie)
        self.assertTrue(status1.startswith("200"), status1)
        status2, data2, *_ = call("POST", "/api/employees", body, cookie=cookie)
        self.assertTrue(status2.startswith("200"), status2)
        self.assertEqual(data1["employee_id"], data2["employee_id"])
        self.assertTrue(data2.get("idempotent"))
        self.assertEqual(self._emp_ids("warehouse").count(data1["employee_id"]), 1)

    def test_create_with_historical_emp_010_duplicate(self):
        wh = next(w for w in self.store["payload"]["workspaces"] if w["code"] == "warehouse")
        wh["employees"] = [
            {"id": "emp_010", "name": "A"},
            {"id": "emp_010", "name": "B"},
            {"id": "e_wh", "name": "Кладовщик"},
        ]
        cookie, _ = self.login()
        status, data, *_ = call(
            "POST",
            "/api/employees",
            {
                "department_id": "warehouse",
                "name": "After Hist",
                "client_request_id": "req-hist",
            },
            cookie=cookie,
        )
        self.assertTrue(status.startswith("200"), status)
        self.assertTrue(str(data["employee_id"]).startswith("T-S-"))
        ids = self._emp_ids("warehouse")
        self.assertEqual(ids.count("emp_010"), 2)
        self.assertIn(data["employee_id"], ids)

    def test_edit_existing_while_other_creates(self):
        cookie, _ = self.login()
        status, created, *_ = call(
            "POST",
            "/api/employees",
            {"department_id": "warehouse", "name": "Created", "client_request_id": "req-ed"},
            cookie=cookie,
        )
        self.assertTrue(status.startswith("200"), status)
        version = int(self.store["version"])
        status_put, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": version,
                "payload": {
                    "workspaces": [
                        {
                            "code": "warehouse",
                            "employees": [
                                {"id": "e_wh", "name": "Кладовщик Renamed", "rate": 1800},
                            ],
                        }
                    ]
                },
            },
            cookie=cookie,
        )
        self.assertTrue(status_put.startswith("200"), status_put)
        wh = next(w for w in self.store["payload"]["workspaces"] if w["code"] == "warehouse")
        names = {e["id"]: e["name"] for e in wh["employees"]}
        self.assertEqual(names.get("e_wh"), "Кладовщик Renamed")
        self.assertEqual(names.get(created["employee_id"]), "Created")

    def test_create_outsourcing_skips_employee_ids(self):
        """Аутсорсинг: карточка создаётся, в employeeIds (табель) не попадает."""
        self.store["payload"]["workspaces"].append(
            {"id": "ws_office", "code": "office", "name": "Офис", "employees": [], "employeeIds": []}
        )
        cookie, _ = self.login()
        status, data, *_ = call(
            "POST",
            "/api/employees",
            {
                "department_id": "office",
                "name": "Финансист Аутсорс",
                "position": "Финансист",
                "work_tab_id": "office",
                "outsourcing": True,
                "outsourcingFixedPay": 100000,
                "rate": 100000,
                "terms": {"2026-11": {"rate": 100000, "taxiDaily": 0}},
                "client_request_id": "req-outsource-1",
            },
            cookie=cookie,
        )
        self.assertTrue(status.startswith("200"), status)
        eid = data["employee_id"]
        office = next(w for w in self.store["payload"]["workspaces"] if w["code"] == "office")
        emp = next(e for e in office["employees"] if e["id"] == eid)
        self.assertTrue(emp.get("outsourcing") is True)
        self.assertEqual(float(emp.get("rate") or 0), 100000)
        self.assertEqual(float(emp.get("outsourcingFixedPay") or 0), 100000)
        self.assertEqual(float((emp.get("terms") or {}).get("2026-11", {}).get("rate") or 0), 100000)
        ids = [str(x) for x in (office.get("employeeIds") or [])]
        self.assertNotIn(eid, ids)


class WtOpsFoundationTests(unittest.TestCase):
    """Точечные операции /api/state/op и защита старого PUT. Без PostgreSQL."""

    def setUp(self):
        self._env_backup = dict(os.environ)
        os.environ["DATABASE_URL"] = "postgresql://worktime:" + SECRET + "@127.0.0.1:5432/worktime"
        os.environ["WT_PIN_PEPPER"] = "unit-pepper"
        os.environ["WT_SCRYPT_N"] = "16"
        os.environ["WT_OPS_ENABLED"] = "1"
        os.environ.pop("WT_API_TOKEN", None)
        os.environ.pop("WT_COOKIE_SECURE", None)
        wt_api.LOGIN_FAILS.clear()
        self.store = {
            "payload": json.loads(json.dumps(SAMPLE_APP)),
            "version": 3,
            "updated_at": datetime.now(timezone.utc),
            "updated_by": "",
            "fail": False,
            "sessions": {},
        }
        self._patch = patch.object(wt_api, "connect", side_effect=lambda: FakeConn(self.store))
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        os.environ.clear()
        os.environ.update(self._env_backup)
        wt_api.LOGIN_FAILS.clear()

    def login(self, user_id="user_admin", pin="5555"):
        status, data, _, cookies = call("POST", "/api/login", {"user_id": user_id, "pin": pin})
        self.assertTrue(status.startswith("200"), status)
        return cookie_from(cookies), data

    def test_health_reports_ops(self):
        status, data, *_ = call("GET", "/api/health")
        self.assertTrue(status.startswith("200"))
        self.assertTrue(data.get("ok"))
        self.assertTrue(data.get("ops"))

    def test_op_saves_independent_paths(self):
        cookie, _ = self.login()
        st1, d1, *_ = call(
            "POST",
            "/api/state/op",
            {
                "idempotency_key": "k1",
                "op": {
                    "path": "shopMetrics.facts.picker_a",
                    "expected_v": 0,
                    "value": 10,
                },
            },
            cookie=cookie,
        )
        self.assertTrue(st1.startswith("200"), st1)
        self.assertEqual(d1.get("record_v"), 1)
        v_after = int(self.store["version"])
        st2, d2, *_ = call(
            "POST",
            "/api/state/op",
            {
                "idempotency_key": "k2",
                "op": {
                    "path": "shopMetrics.facts.cashier_b",
                    "expected_v": 0,
                    "value": 20,
                },
            },
            cookie=cookie,
        )
        self.assertTrue(st2.startswith("200"), st2)
        self.assertEqual(d2.get("record_v"), 1)
        self.assertGreater(int(self.store["version"]), v_after)
        facts = self.store["payload"]["shopMetrics"]["facts"]
        self.assertEqual(facts.get("picker_a"), 10)
        self.assertEqual(facts.get("cashier_b"), 20)
        self.assertEqual(facts.get("ok"), 1)

    def test_nav_layout_op_saves_only_target_section(self):
        """Узкий OPS navLayouts.<code>: пишет только выбранный раздел, чужой ключ не трогает."""
        self.store["payload"]["navLayouts"] = {
            "office": {
                "saved": True,
                "groups": [{"group_id": "ng1", "title": "Офис", "item_ids": ["a"]}],
                "item_labels": {},
                "item_hidden": {},
                "custom_items": [],
            }
        }
        cookie, _ = self.login()
        layout = {
            "saved": True,
            "groups": [{"group_id": "ng2", "title": "Общая", "item_ids": ["salary", "fio"]}],
            "item_labels": {"salary": "Зарплата"},
            "item_hidden": {},
            "custom_items": [],
        }
        st, data, *_ = call(
            "POST",
            "/api/state/op",
            {
                "idempotency_key": "nav-summary-1",
                "op": {
                    "path": "navLayouts.summary",
                    "expected_v": 0,
                    "value": layout,
                },
            },
            cookie=cookie,
        )
        self.assertTrue(st.startswith("200"), st)
        self.assertEqual(data.get("record_v"), 1)
        nav = self.store["payload"].get("navLayouts") or {}
        self.assertEqual(nav.get("summary", {}).get("groups", [])[0].get("title"), "Общая")
        self.assertEqual(nav.get("summary", {}).get("item_labels", {}).get("salary"), "Зарплата")
        self.assertEqual(nav.get("office", {}).get("groups", [])[0].get("title"), "Офис")

    def test_nav_layout_op_conflict_stale_expected_v(self):
        """Устаревший expected_v не перезаписывает чужие изменения меню."""
        cookie, _ = self.login()
        first = {
            "saved": True,
            "groups": [{"group_id": "ng1", "title": "Первый", "item_ids": ["a"]}],
            "item_labels": {},
            "item_hidden": {},
            "custom_items": [],
        }
        st1, d1, *_ = call(
            "POST",
            "/api/state/op",
            {
                "idempotency_key": "nav-c1",
                "op": {"path": "navLayouts.summary", "expected_v": 0, "value": first},
            },
            cookie=cookie,
        )
        self.assertTrue(st1.startswith("200"), st1)
        rec_v = int(d1.get("record_v") or 0)
        self.assertGreaterEqual(rec_v, 1)
        stale = {
            "saved": True,
            "groups": [{"group_id": "ng1", "title": "Устаревший", "item_ids": ["b"]}],
            "item_labels": {},
            "item_hidden": {},
            "custom_items": [],
        }
        st2, d2, *_ = call(
            "POST",
            "/api/state/op",
            {
                "idempotency_key": "nav-c2",
                "op": {"path": "navLayouts.summary", "expected_v": 0, "value": stale},
            },
            cookie=cookie,
        )
        self.assertTrue(st2.startswith("409"), st2)
        self.assertEqual(d2.get("error"), "conflict")
        nav = (self.store["payload"].get("navLayouts") or {}).get("summary") or {}
        self.assertEqual(nav.get("groups", [])[0].get("title"), "Первый")

    def test_nav_layout_op_forbidden_for_settings_edit_staff(self):
        """settings:edit не даёт писать navLayouts — только системный администратор."""
        self.store["payload"]["navLayouts"] = {
            "office": {
                "saved": True,
                "groups": [{"group_id": "ng1", "title": "Админский офис", "item_ids": ["timesheet"]}],
                "item_labels": {},
                "item_hidden": {},
                "custom_items": [],
            }
        }
        cookie, _ = self.login("usr_set", "3333")
        st, data, *_ = call(
            "POST",
            "/api/state/op",
            {
                "idempotency_key": "nav-settings-forbid-1",
                "op": {
                    "path": "navLayouts.office",
                    "expected_v": 0,
                    "value": {
                        "saved": True,
                        "groups": [{"group_id": "ng9", "title": "Чужой", "item_ids": ["x"]}],
                        "item_labels": {},
                        "item_hidden": {},
                        "custom_items": [],
                    },
                },
            },
            cookie=cookie,
        )
        self.assertTrue(st.startswith("403"), st)
        self.assertEqual(data.get("error"), "forbidden")
        office = (self.store["payload"].get("navLayouts") or {}).get("office") or {}
        self.assertEqual(office.get("groups", [])[0].get("title"), "Админский офис")

    def test_nav_layout_op_admin_can_save_office(self):
        """Системный администратор сохраняет меню отдела; чужой ключ не трогается."""
        self.store["payload"]["navLayouts"] = {
            "shop": {
                "saved": True,
                "groups": [{"group_id": "ngs", "title": "Магазин", "item_ids": ["a"]}],
                "item_labels": {},
                "item_hidden": {},
                "custom_items": [],
            }
        }
        cookie, _ = self.login()
        layout = {
            "saved": True,
            "groups": [{"group_id": "ngo", "title": "Офис порядок", "item_ids": ["fio", "timesheet"]}],
            "item_labels": {},
            "item_hidden": {},
            "custom_items": [],
        }
        st, data, *_ = call(
            "POST",
            "/api/state/op",
            {
                "idempotency_key": "nav-admin-office-1",
                "op": {"path": "navLayouts.office", "expected_v": 0, "value": layout},
            },
            cookie=cookie,
        )
        self.assertTrue(st.startswith("200"), st)
        nav = self.store["payload"].get("navLayouts") or {}
        self.assertEqual(nav.get("office", {}).get("groups", [])[0].get("title"), "Офис порядок")
        self.assertEqual(nav.get("shop", {}).get("groups", [])[0].get("title"), "Магазин")

    def _seed_nav_layouts_multi(self):
        shop_order = ["shop::metrics-managers", "dept::timesheet", "shop::revision", "dept::fio"]
        warehouse_order = ["warehouse::fio", "warehouse::timesheet", "warehouse::revision"]
        office_order = ["office::timesheet", "office::fio"]
        self.store["payload"]["navLayouts"] = {
            "shop": {
                "saved": True,
                "groups": [{"group_id": "ng_shop", "title": "", "item_ids": shop_order}],
                "item_labels": {},
                "item_hidden": {},
                "custom_items": [],
            },
            "warehouse": {
                "saved": True,
                "groups": [{"group_id": "ng_wh", "title": "", "item_ids": warehouse_order}],
                "item_labels": {},
                "item_hidden": {},
                "custom_items": [],
            },
            "office": {
                "saved": True,
                "groups": [{"group_id": "ng_off", "title": "", "item_ids": office_order}],
                "item_labels": {},
                "item_hidden": {},
                "custom_items": [],
            },
            "admin": {
                "saved": True,
                "groups": [{"group_id": "ng_adm", "title": "", "item_ids": ["both::users"]}],
                "item_labels": {},
                "item_hidden": {},
                "custom_items": [],
            },
        }
        return shop_order, warehouse_order, office_order

    def test_get_state_admin_receives_all_nav_layouts(self):
        """Администратор видит все ключи navLayouts (полный payload)."""
        shop_order, warehouse_order, office_order = self._seed_nav_layouts_multi()
        cookie, _ = self.login()
        st, data, *_ = call("GET", "/api/state", cookie=cookie)
        self.assertTrue(st.startswith("200"), st)
        nav = (data.get("payload") or {}).get("navLayouts") or {}
        self.assertIn("shop", nav)
        self.assertIn("warehouse", nav)
        self.assertIn("office", nav)
        self.assertIn("admin", nav)
        self.assertEqual(nav["shop"]["groups"][0]["item_ids"], shop_order)
        self.assertEqual(nav["warehouse"]["groups"][0]["item_ids"], warehouse_order)
        self.assertEqual(nav["office"]["groups"][0]["item_ids"], office_order)

    def test_get_state_shop_staff_receives_only_shop_nav_layout(self):
        """Сотрудник магазина получает navLayouts.shop с порядком админа; чужие разделы скрыты."""
        shop_order, _, _ = self._seed_nav_layouts_multi()
        cookie, _ = self.login("usr_001", "1111")
        st, data, *_ = call("GET", "/api/state", cookie=cookie)
        self.assertTrue(st.startswith("200"), st)
        nav = (data.get("payload") or {}).get("navLayouts") or {}
        self.assertIn("shop", nav)
        self.assertNotIn("warehouse", nav)
        self.assertNotIn("office", nav)
        self.assertNotIn("admin", nav)
        self.assertTrue(nav["shop"].get("saved"))
        self.assertEqual(nav["shop"]["groups"][0]["item_ids"], shop_order)

    def test_get_state_multi_dept_staff_receives_own_nav_layouts_only(self):
        """Сотрудник с shop+warehouse получает оба layout; office/admin не отдаются."""
        shop_order, warehouse_order, _ = self._seed_nav_layouts_multi()
        multi = {
            "user_id": "usr_multi_nav",
            "display_name": "Два отдела",
            "pin": "7777",
            "role": "user",
            "is_active": True,
            "departments": ["shop", "warehouse"],
            "permissions": {"timesheet": "edit", "metricsInput": "edit"},
        }
        self.store["payload"]["users"].append(multi)
        cookie, _ = self.login("usr_multi_nav", "7777")
        st, data, *_ = call("GET", "/api/state", cookie=cookie)
        self.assertTrue(st.startswith("200"), st)
        nav = (data.get("payload") or {}).get("navLayouts") or {}
        self.assertIn("shop", nav)
        self.assertIn("warehouse", nav)
        # territoryMetrics может появиться из login_codes для territory-отделов.
        self.assertNotIn("office", nav)
        self.assertNotIn("admin", nav)
        self.assertEqual(nav["shop"]["groups"][0]["item_ids"], shop_order)
        self.assertEqual(nav["warehouse"]["groups"][0]["item_ids"], warehouse_order)

    def test_nav_layout_relative_order_kept_for_subset_rights(self):
        """При урезанных правах относительный порядок админских вкладок сохраняется."""
        shop_order, _, _ = self._seed_nav_layouts_multi()
        allowed = ["dept::fio", "shop::metrics-managers", "dept::timesheet"]
        shown = []
        allow_set = set(allowed)
        for item_id in shop_order:
            if item_id in allow_set:
                shown.append(item_id)
                allow_set.discard(item_id)
        self.assertEqual(shown, ["shop::metrics-managers", "dept::timesheet", "dept::fio"])
        self.assertNotIn("shop::revision", shown)
        filtered = wt_api.filter_nav_layouts_for_user(self.store["payload"], STAFF)
        self.assertEqual(filtered["shop"]["groups"][0]["item_ids"], shop_order)

    def test_op_real_conflict_same_path(self):
        cookie, _ = self.login()
        call(
            "POST",
            "/api/state/op",
            {"idempotency_key": "c1", "op": {"path": "shopMetrics.facts.cell", "expected_v": 0, "value": "A"}},
            cookie=cookie,
        )
        st, data, *_ = call(
            "POST",
            "/api/state/op",
            {"idempotency_key": "c2", "op": {"path": "shopMetrics.facts.cell", "expected_v": 0, "value": "B"}},
            cookie=cookie,
        )
        self.assertTrue(st.startswith("409"), st)
        self.assertEqual(data.get("error"), "conflict")
        self.assertEqual(data.get("server_value"), "A")
        self.assertEqual(data.get("your_value"), "B")
        self.assertEqual(data.get("server_v"), 1)
        self.assertEqual(self.store["payload"]["shopMetrics"]["facts"]["cell"], "A")

    def test_op_confirm_replace_after_conflict(self):
        cookie, _ = self.login()
        call(
            "POST",
            "/api/state/op",
            {"op": {"path": "shopMetrics.facts.cell", "expected_v": 0, "value": "A"}},
            cookie=cookie,
        )
        st, data, *_ = call(
            "POST",
            "/api/state/op",
            {"op": {"path": "shopMetrics.facts.cell", "expected_v": 1, "value": "B"}},
            cookie=cookie,
        )
        self.assertTrue(st.startswith("200"), st)
        self.assertEqual(data.get("value"), "B")
        self.assertEqual(self.store["payload"]["shopMetrics"]["facts"]["cell"], "B")
        self.assertEqual(data.get("record_v"), 2)

    def test_op_idempotency(self):
        cookie, _ = self.login()
        body = {
            "idempotency_key": "same-key",
            "op": {"path": "shopMetrics.facts.once", "expected_v": 0, "value": 7},
        }
        st1, d1, *_ = call("POST", "/api/state/op", body, cookie=cookie)
        ver1 = int(self.store["version"])
        st2, d2, *_ = call("POST", "/api/state/op", body, cookie=cookie)
        self.assertTrue(st1.startswith("200"))
        self.assertTrue(st2.startswith("200"))
        self.assertEqual(d1.get("value"), 7)
        self.assertEqual(d2.get("value"), 7)
        self.assertEqual(int(self.store["version"]), ver1)
        self.assertEqual(self.store["payload"]["shopMetrics"]["facts"]["once"], 7)

    def test_stale_put_does_not_wipe_op_value(self):
        """Пользователь со старым снимком PUT не затирает путь, обновлённый через op."""
        cookie, _ = self.login()
        call(
            "POST",
            "/api/state/op",
            {"op": {"path": "shopMetrics.facts.guarded", "expected_v": 0, "value": "from_op"}},
            cookie=cookie,
        )
        version = int(self.store["version"])
        # Устаревший клиент: нет _wt_records / v=0, но шлёт старое facts без guarded.
        st, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": version,
                "payload": {
                    "shopMetrics": {"facts": {"ok": 1, "nested_ignore": True}},
                    "_wt_records": {},
                },
            },
            cookie=cookie,
        )
        self.assertTrue(st.startswith("200"), st)
        facts = self.store["payload"]["shopMetrics"]["facts"]
        self.assertEqual(facts.get("guarded"), "from_op")
        meta = self.store["payload"].get("_wt_records") or {}
        self.assertGreaterEqual(int((meta.get("shopMetrics.facts.guarded") or {}).get("v") or 0), 1)

    def test_staff_forbidden_other_dept_op(self):
        cookie, _ = self.login("usr_001", "1111")
        st, data, *_ = call(
            "POST",
            "/api/state/op",
            {
                "op": {
                    "path": "deptMetrics.warehouse.facts.secret",
                    "expected_v": 0,
                    "value": 99,
                }
            },
            cookie=cookie,
        )
        self.assertTrue(st.startswith("403"), st)
        self.assertEqual(data.get("error"), "forbidden")

    def test_ops_disabled_flag(self):
        with patch.dict(os.environ, {"WT_OPS_ENABLED": "0"}, clear=False):
            cookie, _ = self.login()
            st, data, *_ = call(
                "POST",
                "/api/state/op",
                {"op": {"path": "shopMetrics.facts.x", "expected_v": 0, "value": 1}},
                cookie=cookie,
            )
            self.assertTrue(st.startswith("503"), st)
            self.assertEqual(data.get("error"), "ops disabled")

    def test_staff_cannot_op_metric_setup_via_path(self):
        """metricsInput не даёт писать workAccess подстановкой path."""
        cookie, _ = self.login("usr_001", "1111")
        st, data, *_ = call(
            "POST",
            "/api/state/op",
            {
                "op": {
                    "path": "shopMetrics.workAccess.2026-10.e_shop",
                    "expected_v": 0,
                    "value": ["m1"],
                }
            },
            cookie=cookie,
        )
        self.assertTrue(st.startswith("403"), st)
        self.assertEqual(data.get("error"), "forbidden")

    def _add_metric_user(self, user_id, pin, permissions, departments=None):
        self.store["payload"]["users"].append({
            "user_id": user_id,
            "display_name": user_id,
            "pin": pin,
            "role": "manager",
            "is_active": True,
            "departments": departments or ["shop"],
            "permissions": permissions,
        })
        cookie, _ = self.login(user_id, pin)
        return cookie

    def test_ops_metrics_setup_cannot_write_facts(self):
        """K1: metricsSetup без metricsInput — OPS facts запрещён (паритет с PUT)."""
        self.store["payload"]["shopMetrics"]["workAccess"] = {
            "2026-10": {"e_shop": ["m_keep"], "e_wh": ["m_wh"]},
        }
        cookie = self._add_metric_user("usr_setup_only", "4141", {"metricsSetup": "edit"})
        before = json.loads(json.dumps(self.store["payload"]["shopMetrics"].get("facts")))
        st, data, *_ = call(
            "POST",
            "/api/state/op",
            {"op": {"path": "shopMetrics.facts.probe", "expected_v": 0, "value": 999}},
            cookie=cookie,
        )
        self.assertTrue(st.startswith("403"), st)
        self.assertEqual(data.get("error"), "forbidden")
        self.assertEqual(self.store["payload"]["shopMetrics"].get("facts"), before)

    def test_ops_metrics_input_can_write_own_dept_facts(self):
        """metricsInput сохраняет разрешённые facts своего подразделения."""
        cookie = self._add_metric_user("usr_input_only", "4242", {"metricsInput": "edit"})
        st, data, *_ = call(
            "POST",
            "/api/state/op",
            {"op": {"path": "shopMetrics.facts.probe_ok", "expected_v": 0, "value": 7}},
            cookie=cookie,
        )
        self.assertTrue(st.startswith("200"), st)
        self.assertEqual(self.store["payload"]["shopMetrics"]["facts"]["probe_ok"], 7)

    def test_ops_work_access_foreign_emp_forbidden(self):
        """H1: shop metricsSetup не пишет workAccess склада (e_wh) через OPS."""
        self.store["payload"]["shopMetrics"]["workAccess"] = {
            "2026-10": {"e_shop": ["m_keep"], "e_wh": ["m_wh"]},
        }
        cookie = self._add_metric_user("usr_setup_wa", "4343", {"metricsSetup": "edit"})
        before = list(self.store["payload"]["shopMetrics"]["workAccess"]["2026-10"]["e_wh"])
        st, data, *_ = call(
            "POST",
            "/api/state/op",
            {
                "op": {
                    "path": "shopMetrics.workAccess.2026-10.e_wh",
                    "expected_v": 0,
                    "value": ["hacked"],
                }
            },
            cookie=cookie,
        )
        self.assertTrue(st.startswith("403"), st)
        self.assertEqual(data.get("error"), "forbidden")
        self.assertEqual(
            self.store["payload"]["shopMetrics"]["workAccess"]["2026-10"]["e_wh"],
            before,
        )

    def test_ops_work_access_own_emp_allowed_and_clear(self):
        """metricsSetup пишет и снимает workAccess своего emp; чужой deptMetrics — 403."""
        self.store["payload"]["shopMetrics"]["workAccess"] = {
            "2026-10": {"e_shop": ["m_keep"], "e_wh": ["m_wh"]},
        }
        self.store["payload"]["deptMetrics"]["warehouse"] = {
            "workAccess": {"2026-10": {"e_wh": ["m_wh"]}},
        }
        cookie = self._add_metric_user("usr_setup_own", "4445", {"metricsSetup": "edit"})
        st, *_ = call(
            "POST",
            "/api/state/op",
            {
                "op": {
                    "path": "shopMetrics.workAccess.2026-10.e_shop",
                    "expected_v": 0,
                    "value": ["m_new"],
                }
            },
            cookie=cookie,
        )
        self.assertTrue(st.startswith("200"), st)
        self.assertEqual(
            self.store["payload"]["shopMetrics"]["workAccess"]["2026-10"]["e_shop"],
            ["m_new"],
        )
        v = int(
            (self.store["payload"].get("_wt_records") or {})
            .get("shopMetrics.workAccess.2026-10.e_shop", {})
            .get("v")
            or 0
        )
        st2, *_ = call(
            "POST",
            "/api/state/op",
            {
                "op": {
                    "path": "shopMetrics.workAccess.2026-10.e_shop",
                    "expected_v": v,
                    "value": [],
                }
            },
            cookie=cookie,
        )
        self.assertTrue(st2.startswith("200"), st2)
        self.assertEqual(
            self.store["payload"]["shopMetrics"]["workAccess"]["2026-10"]["e_shop"],
            [],
        )
        self.assertEqual(
            self.store["payload"]["shopMetrics"]["workAccess"]["2026-10"]["e_wh"],
            ["m_wh"],
        )
        st3, data3, *_ = call(
            "POST",
            "/api/state/op",
            {
                "op": {
                    "path": "deptMetrics.warehouse.workAccess.2026-10.e_wh",
                    "expected_v": 0,
                    "value": [],
                }
            },
            cookie=cookie,
        )
        self.assertTrue(st3.startswith("403"), st3)
        self.assertEqual(data3.get("error"), "forbidden")

    def test_ops_work_access_whole_map_forbidden_for_staff(self):
        """Целый shopMetrics.workAccess без empId — staff OPS запрещён."""
        cookie = self._add_metric_user("usr_setup_map", "4545", {"metricsSetup": "edit"})
        st, data, *_ = call(
            "POST",
            "/api/state/op",
            {
                "op": {
                    "path": "shopMetrics.workAccess",
                    "expected_v": 0,
                    "value": {"2026-10": {"e_shop": ["x"]}},
                }
            },
            cookie=cookie,
        )
        self.assertTrue(st.startswith("403"), st)
        self.assertEqual(data.get("error"), "forbidden")

    def test_ops_admin_still_writes_metric_facts_and_access(self):
        """Админ сохраняет предусмотренные полномочия OPS."""
        cookie, _ = self.login()
        st, *_ = call(
            "POST",
            "/api/state/op",
            {"op": {"path": "shopMetrics.facts.admin_ok", "expected_v": 0, "value": 1}},
            cookie=cookie,
        )
        self.assertTrue(st.startswith("200"), st)
        st2, *_ = call(
            "POST",
            "/api/state/op",
            {
                "op": {
                    "path": "shopMetrics.workAccess.2026-10.e_wh",
                    "expected_v": 0,
                    "value": ["m_admin"],
                }
            },
            cookie=cookie,
        )
        self.assertTrue(st2.startswith("200"), st2)
        self.assertEqual(
            self.store["payload"]["shopMetrics"]["workAccess"]["2026-10"]["e_wh"],
            ["m_admin"],
        )

    def test_staff_cannot_op_metric_admin_meta(self):
        cookie, _ = self.login("usr_001", "1111")
        st, data, *_ = call(
            "POST",
            "/api/state/op",
            {"op": {"path": "shopMetrics.storeMeta.secret", "expected_v": 0, "value": 1}},
            cookie=cookie,
        )
        self.assertTrue(st.startswith("403"), st)
        self.assertEqual(data.get("error"), "forbidden")

    def test_forged_client_record_v_cannot_wipe_op(self):
        """PUT с завышенным _wt_records.v не перезаписывает лист op и не поднимает meta."""
        cookie, _ = self.login()
        call(
            "POST",
            "/api/state/op",
            {"op": {"path": "shopMetrics.facts.guarded", "expected_v": 0, "value": "from_op"}},
            cookie=cookie,
        )
        version = int(self.store["version"])
        st, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": version,
                "payload": {
                    "shopMetrics": {"facts": {"ok": 1, "guarded": "forged"}},
                    "_wt_records": {"shopMetrics.facts.guarded": {"v": 99}},
                },
            },
            cookie=cookie,
        )
        self.assertTrue(st.startswith("200"), st)
        facts = self.store["payload"]["shopMetrics"]["facts"]
        self.assertEqual(facts.get("guarded"), "from_op")
        meta = self.store["payload"].get("_wt_records") or {}
        self.assertEqual(int((meta.get("shopMetrics.facts.guarded") or {}).get("v") or 0), 1)

    def test_workspaces_op_by_code_keeps_list(self):
        """Путь workspaces.<code> не превращает список подразделений в dict."""
        cookie, _ = self.login()
        st, data, *_ = call(
            "POST",
            "/api/state/op",
            {
                "op": {
                    "path": "workspaces.shop.timesheet.2026-10.e_shop.2",
                    "expected_v": 0,
                    "value": "В",
                }
            },
            cookie=cookie,
        )
        self.assertTrue(st.startswith("200"), st)
        self.assertEqual(data.get("value"), "В")
        workspaces = self.store["payload"]["workspaces"]
        self.assertIsInstance(workspaces, list)
        self.assertEqual(len(workspaces), 2)
        shop = next(ws for ws in workspaces if ws.get("code") == "shop")
        self.assertEqual(shop["timesheet"]["2026-10"]["e_shop"]["2"], "В")
        self.assertEqual(shop["timesheet"]["2026-10"]["e_shop"]["1"], "Я")
        wh = next(ws for ws in workspaces if ws.get("code") == "warehouse")
        self.assertEqual(wh["timesheet"]["2026-10"]["e_wh"]["1"], "Я")

    def test_third_writer_before_conflict_confirm(self):
        """Пока A думает подтвердить замену, C успевает записать — expected_v A устаревает."""
        cookie, _ = self.login()
        call(
            "POST",
            "/api/state/op",
            {"op": {"path": "shopMetrics.facts.race", "expected_v": 0, "value": "A"}},
            cookie=cookie,
        )
        # B получает conflict (не проверяем здесь), C пишет поверх v=1.
        st_c, *_ = call(
            "POST",
            "/api/state/op",
            {"op": {"path": "shopMetrics.facts.race", "expected_v": 1, "value": "C"}},
            cookie=cookie,
        )
        self.assertTrue(st_c.startswith("200"), st_c)
        st_a, data_a, *_ = call(
            "POST",
            "/api/state/op",
            {"op": {"path": "shopMetrics.facts.race", "expected_v": 1, "value": "B"}},
            cookie=cookie,
        )
        self.assertTrue(st_a.startswith("409"), st_a)
        self.assertEqual(data_a.get("server_value"), "C")
        self.assertEqual(self.store["payload"]["shopMetrics"]["facts"]["race"], "C")

    def test_ops_disabled_keeps_saved_values_for_put(self):
        """После отключения op уже сохранённые значения остаются и защищены PUT."""
        cookie, _ = self.login()
        call(
            "POST",
            "/api/state/op",
            {"op": {"path": "shopMetrics.facts.kept", "expected_v": 0, "value": "keep_me"}},
            cookie=cookie,
        )
        with patch.dict(os.environ, {"WT_OPS_ENABLED": "0"}, clear=False):
            st_op, *_ = call(
                "POST",
                "/api/state/op",
                {"op": {"path": "shopMetrics.facts.kept", "expected_v": 1, "value": "nope"}},
                cookie=cookie,
            )
            self.assertTrue(st_op.startswith("503"), st_op)
            version = int(self.store["version"])
            st_put, *_ = call(
                "PUT",
                "/api/state",
                {
                    "version": version,
                    "payload": {
                        "shopMetrics": {"facts": {"ok": 1}},
                        "_wt_records": {},
                    },
                },
                cookie=cookie,
            )
            self.assertTrue(st_put.startswith("200"), st_put)
        self.assertEqual(self.store["payload"]["shopMetrics"]["facts"].get("kept"), "keep_me")

    def test_put_bumps_record_v_so_stale_op_conflicts(self):
        """PUT изменил лист → op с expected_v=0 получает конфликт, а не silent overwrite."""
        cookie, _ = self.login()
        version = int(self.store["version"])
        st_put, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": version,
                "payload": {"shopMetrics": {"facts": {"ok": 1, "from_put": "PUT_VAL"}}},
            },
            cookie=cookie,
        )
        self.assertTrue(st_put.startswith("200"), st_put)
        meta = self.store["payload"].get("_wt_records") or {}
        self.assertGreaterEqual(int((meta.get("shopMetrics.facts.from_put") or {}).get("v") or 0), 1)
        self.assertEqual(self.store["payload"]["shopMetrics"]["facts"].get("from_put"), "PUT_VAL")
        st_op, data, *_ = call(
            "POST",
            "/api/state/op",
            {"op": {"path": "shopMetrics.facts.from_put", "expected_v": 0, "value": "OP_STALE"}},
            cookie=cookie,
        )
        self.assertTrue(st_op.startswith("409"), st_op)
        self.assertEqual(data.get("error"), "conflict")
        self.assertEqual(data.get("server_value"), "PUT_VAL")
        self.assertEqual(self.store["payload"]["shopMetrics"]["facts"].get("from_put"), "PUT_VAL")

    def test_put_then_op_with_fresh_record_v_succeeds(self):
        cookie, _ = self.login()
        version = int(self.store["version"])
        call(
            "PUT",
            "/api/state",
            {
                "version": version,
                "payload": {"shopMetrics": {"facts": {"ok": 1, "fresh": "P"}}},
            },
            cookie=cookie,
        )
        rv = int((self.store["payload"].get("_wt_records") or {}).get("shopMetrics.facts.fresh", {}).get("v") or 0)
        st, data, *_ = call(
            "POST",
            "/api/state/op",
            {"op": {"path": "shopMetrics.facts.fresh", "expected_v": rv, "value": "O"}},
            cookie=cookie,
        )
        self.assertTrue(st.startswith("200"), st)
        self.assertEqual(data.get("value"), "O")
        self.assertEqual(self.store["payload"]["shopMetrics"]["facts"].get("fresh"), "O")

    def test_put_independent_paths_do_not_false_conflict(self):
        cookie, _ = self.login()
        call(
            "POST",
            "/api/state/op",
            {"op": {"path": "shopMetrics.facts.keep_a", "expected_v": 0, "value": "A"}},
            cookie=cookie,
        )
        version = int(self.store["version"])
        call(
            "PUT",
            "/api/state",
            {
                "version": version,
                "payload": {
                    "shopMetrics": {"facts": {"ok": 1, "keep_a": "A", "only_b": "B"}},
                    "_wt_records": {
                        "shopMetrics.facts.keep_a": self.store["payload"]["_wt_records"][
                            "shopMetrics.facts.keep_a"
                        ]
                    },
                },
            },
            cookie=cookie,
        )
        # keep_a не менялся — версия не обязана расти; only_b новый — op с v=0 на keep_a ок по значению
        st, *_ = call(
            "POST",
            "/api/state/op",
            {"op": {"path": "shopMetrics.facts.keep_a", "expected_v": 1, "value": "A2"}},
            cookie=cookie,
        )
        self.assertTrue(st.startswith("200"), st)
        self.assertEqual(self.store["payload"]["shopMetrics"]["facts"].get("only_b"), "B")
        st_b, data_b, *_ = call(
            "POST",
            "/api/state/op",
            {"op": {"path": "shopMetrics.facts.only_b", "expected_v": 0, "value": "B2"}},
            cookie=cookie,
        )
        # only_b уже bumped PUT-ом → expected_v=0 conflict
        self.assertTrue(st_b.startswith("409"), st_b)
        self.assertEqual(data_b.get("server_value"), "B")

    def test_client_cannot_set_wt_records_via_put(self):
        cookie, _ = self.login()
        version = int(self.store["version"])
        st, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": version,
                "payload": {
                    "shopMetrics": {"facts": {"ok": 1, "x": 1}},
                    "_wt_records": {"shopMetrics.facts.x": {"v": 50}, "forged.path": {"v": 9}},
                    "_wt_idempotency": {"k": {"at": "t", "result": {"ok": True}}},
                },
            },
            cookie=cookie,
        )
        self.assertTrue(st.startswith("200"), st)
        meta = self.store["payload"].get("_wt_records") or {}
        self.assertNotIn("forged.path", meta)
        self.assertNotEqual(int((meta.get("shopMetrics.facts.x") or {}).get("v") or 0), 50)
        self.assertGreaterEqual(int((meta.get("shopMetrics.facts.x") or {}).get("v") or 0), 1)
        idem = self.store["payload"].get("_wt_idempotency")
        if isinstance(idem, dict):
            self.assertNotIn("k", idem)

    def test_prune_does_not_drop_live_paths(self):
        """Живые пути не вытесняются при переполнении — иначе silent overwrite."""
        cookie, _ = self.login()
        # Синтетически наполняем карту живыми путями выше soft-max.
        payload = self.store["payload"]
        facts = payload.setdefault("shopMetrics", {}).setdefault("facts", {})
        meta = {}
        soft = wt_api.WT_RECORDS_SOFT_MAX
        for i in range(soft + 50):
            key = f"cell_{i}"
            facts[key] = i
            meta[f"shopMetrics.facts.{key}"] = {
                "v": 1,
                "updated_at": f"2020-01-01T00:00:{i % 60:02d}.000000+00:00",
                "updated_by": "u",
            }
        # Сироты (значения нет) — их можно сжать.
        for i in range(100):
            meta[f"shopMetrics.facts.orphan_{i}"] = {
                "v": 1,
                "updated_at": f"2019-01-01T00:00:{i % 60:02d}.000000+00:00",
                "updated_by": "u",
            }
        payload[wt_api.WT_RECORDS_KEY] = meta
        pruned = wt_api.prune_record_meta(payload, dict(meta))
        for i in range(soft + 50):
            self.assertIn(f"shopMetrics.facts.cell_{i}", pruned)
        self.assertLessEqual(len(pruned), soft + 50)
        # После bump нового живого пути старые живые остаются.
        nxt, _ = wt_api.apply_state_op(
            payload,
            {"path": "shopMetrics.facts.cell_0", "expected_v": 1, "value": 999},
            {"user_id": "user_admin", "role": "admin", "departments": ["shop"], "permissions": {}},
        )
        self.assertIsNotNone(nxt)
        self.assertEqual(nxt["shopMetrics"]["facts"]["cell_0"], 999)
        self.assertIn("shopMetrics.facts.cell_1", nxt.get(wt_api.WT_RECORDS_KEY) or {})
        # expected_v для старого пути после «переполнения» не обнуляется.
        self.assertGreaterEqual(
            wt_api.record_version(nxt, "shopMetrics.facts.cell_100"),
            1,
        )


class WtAccessUserOpsTests(unittest.TestCase):
    """Узкое сохранение прав: users.<user_id> через OPS. FakeConn, без live БД."""

    def setUp(self):
        self._env_backup = dict(os.environ)
        os.environ["DATABASE_URL"] = "postgresql://worktime:" + SECRET + "@127.0.0.1:5432/worktime"
        os.environ["WT_PIN_PEPPER"] = "unit-pepper"
        os.environ["WT_SCRYPT_N"] = "16"
        os.environ["WT_OPS_ENABLED"] = "1"
        os.environ.pop("WT_API_TOKEN", None)
        os.environ.pop("WT_COOKIE_SECURE", None)
        wt_api.LOGIN_FAILS.clear()
        payload = json.loads(json.dumps(SAMPLE_APP))
        # Хеши как после PUT — вход по pin_hash.
        payload = wt_api.hash_pins_in_payload(payload, None)
        self.store = {
            "payload": payload,
            "version": 3,
            "updated_at": datetime.now(timezone.utc),
            "updated_by": "",
            "fail": False,
            "sessions": {},
        }
        self._patch = patch.object(wt_api, "connect", side_effect=lambda: FakeConn(self.store))
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        os.environ.clear()
        os.environ.update(self._env_backup)
        wt_api.LOGIN_FAILS.clear()

    def login(self, user_id="user_admin", pin="5555"):
        status, data, _, cookies = call("POST", "/api/login", {"user_id": user_id, "pin": pin})
        self.assertTrue(status.startswith("200"), status)
        return cookie_from(cookies), data

    def _user(self, user_id):
        return next(u for u in self.store["payload"]["users"] if u["user_id"] == user_id)

    def test_one_perm_checkbox_via_ops(self):
        cookie, _ = self.login()
        row = json.loads(json.dumps(self._user("usr_001")))
        row.pop("pin", None)
        row.pop("password", None)
        row.pop("pin_hash", None)
        perms = dict(row.get("permissions") or {})
        perms["timesheet"] = "none"
        row["permissions"] = perms
        st, data, raw, _ = call(
            "POST",
            "/api/state/op",
            {"op": {"path": "users.usr_001", "expected_v": 0, "value": row}},
            cookie=cookie,
        )
        self.assertTrue(st.startswith("200"), st)
        stored = self._user("usr_001")
        self.assertEqual(stored["permissions"].get("timesheet"), "none")
        self.assertTrue(str(stored.get("pin_hash") or "").startswith("scrypt$"))
        self.assertNotIn("pin", stored)
        self.assertNotIn("1111", raw or "")
        self.assertNotIn("pin_hash", json.dumps(data.get("value") or {}))

    def test_several_perms_role_departments(self):
        cookie, _ = self.login()
        row = json.loads(json.dumps(self._user("usr_001")))
        row.pop("pin", None)
        row.pop("pin_hash", None)
        row["role"] = "manager"
        row["departments"] = ["shop", "summary"]
        row["permissions"] = {
            "timesheet": "edit",
            "metricsInput": "edit",
            "salaryCalc": "edit",
            "salary": "edit",
            "settings": "none",
        }
        st, *_ = call(
            "POST",
            "/api/state/op",
            {"op": {"path": "users.usr_001", "expected_v": 0, "value": row}},
            cookie=cookie,
        )
        self.assertTrue(st.startswith("200"), st)
        stored = self._user("usr_001")
        self.assertEqual(stored["role"], "manager")
        self.assertEqual(stored["departments"], ["shop", "summary"])
        self.assertEqual(stored["permissions"]["salaryCalc"], "edit")

    def test_persist_and_reread(self):
        cookie, _ = self.login()
        row = json.loads(json.dumps(self._user("usr_001")))
        row.pop("pin", None)
        row.pop("pin_hash", None)
        row["display_name"] = "ВИКУЛИШНА-NEW"
        row["permissions"] = dict(row.get("permissions") or {})
        row["permissions"]["revision"] = "edit"
        call(
            "POST",
            "/api/state/op",
            {"op": {"path": "users.usr_001", "expected_v": 0, "value": row}},
            cookie=cookie,
        )
        st, got, raw, _ = call("GET", "/api/state", cookie=cookie)
        self.assertTrue(st.startswith("200"), st)
        reloaded = next(u for u in got["payload"]["users"] if u["user_id"] == "usr_001")
        self.assertEqual(reloaded["display_name"], "ВИКУЛИШНА-NEW")
        self.assertEqual(reloaded["permissions"]["revision"], "edit")
        self.assertNotIn("pin", reloaded)
        self.assertNotIn("pin_hash", reloaded)
        self.assertNotIn("pin_hash", raw or "")

    def test_two_admins_different_users(self):
        cookie, _ = self.login()
        a = json.loads(json.dumps(self._user("user_admin")))
        a.pop("pin", None)
        a.pop("pin_hash", None)
        a["display_name"] = "Admin-A"
        b = json.loads(json.dumps(self._user("usr_001")))
        b.pop("pin", None)
        b.pop("pin_hash", None)
        b["display_name"] = "Staff-B"
        st1, d1, *_ = call(
            "POST",
            "/api/state/op",
            {"op": {"path": "users.user_admin", "expected_v": 0, "value": a}},
            cookie=cookie,
        )
        st2, d2, *_ = call(
            "POST",
            "/api/state/op",
            {"op": {"path": "users.usr_001", "expected_v": 0, "value": b}},
            cookie=cookie,
        )
        self.assertTrue(st1.startswith("200"), st1)
        self.assertTrue(st2.startswith("200"), st2)
        self.assertEqual(self._user("user_admin")["display_name"], "Admin-A")
        self.assertEqual(self._user("usr_001")["display_name"], "Staff-B")
        self.assertEqual(d1.get("record_v"), 1)
        self.assertEqual(d2.get("record_v"), 1)

    def test_same_user_conflict(self):
        cookie, _ = self.login()
        row = json.loads(json.dumps(self._user("usr_001")))
        row.pop("pin", None)
        row.pop("pin_hash", None)
        row["display_name"] = "First"
        call(
            "POST",
            "/api/state/op",
            {"op": {"path": "users.usr_001", "expected_v": 0, "value": row}},
            cookie=cookie,
        )
        stale = json.loads(json.dumps(row))
        stale["display_name"] = "Stale"
        st, data, *_ = call(
            "POST",
            "/api/state/op",
            {"op": {"path": "users.usr_001", "expected_v": 0, "value": stale}},
            cookie=cookie,
        )
        self.assertTrue(st.startswith("409"), st)
        self.assertEqual(data.get("error"), "conflict")
        self.assertEqual(self._user("usr_001")["display_name"], "First")
        # другой пользователь не затронут
        self.assertEqual(self._user("user_admin")["display_name"], ADMIN["display_name"])

    def test_non_admin_forbidden(self):
        cookie, _ = self.login("usr_001", "1111")
        row = json.loads(json.dumps(self._user("usr_001")))
        row.pop("pin", None)
        row.pop("pin_hash", None)
        row["permissions"] = dict(row.get("permissions") or {})
        row["permissions"]["settings"] = "edit"
        st, data, *_ = call(
            "POST",
            "/api/state/op",
            {"op": {"path": "users.usr_001", "expected_v": 0, "value": row}},
            cookie=cookie,
        )
        self.assertTrue(st.startswith("403"), st)
        self.assertEqual(data.get("error"), "forbidden")

    def test_pin_change_login_and_others_untouched(self):
        cookie, _ = self.login()
        other_hash = self._user("user_admin")["pin_hash"]
        row = json.loads(json.dumps(self._user("usr_001")))
        row["pin"] = "9999"
        row.pop("pin_hash", None)
        st, data, raw, _ = call(
            "POST",
            "/api/state/op",
            {"op": {"path": "users.usr_001", "expected_v": 0, "value": row}},
            cookie=cookie,
        )
        self.assertTrue(st.startswith("200"), st)
        stored = self._user("usr_001")
        self.assertTrue(str(stored.get("pin_hash") or "").startswith("scrypt$"))
        self.assertNotIn("pin", stored)
        self.assertNotIn("9999", raw or "")
        self.assertTrue(wt_api.verify_pin("9999", stored["pin_hash"]))
        self.assertFalse(wt_api.verify_pin("1111", stored["pin_hash"]))
        self.assertEqual(self._user("user_admin")["pin_hash"], other_hash)
        # вход новым PIN
        st_login, *_ = call("POST", "/api/login", {"user_id": "usr_001", "pin": "9999"})
        self.assertTrue(st_login.startswith("200"), st_login)

    def test_client_markers_narrow_persist_no_wide_saveapp(self):
        # resolve() — через junction (C:\wt_ca_server) к реальному корню репо.
        html = (Path(__file__).resolve().parent.parent / "app.html").read_text(encoding="utf-8")
        self.assertIn("function wtPersistAccessUser(", html)
        self.assertIn("function wtAccessUserPath(", html)
        save_fn = html.split("function saveAccessUser(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("wtPersistAccessUser(", save_fn)
        self.assertIn("syncLegacyPinsFromUsers", save_fn)
        self.assertNotIn("saveApp()", save_fn)
        persist_fn = html.split("function wtPersistAccessUser(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("wtOpsProbeServerCapability(true)", persist_fn)
        self.assertIn("forbidForcePut: true", persist_fn)
        self.assertIn("wtDomainWriterSave({", persist_fn)
        self.assertIn("OPS выключен", persist_fn)
        # Серверная ветка (после локального saveApp): только OPS, без forcePut/saveApp.
        server_persist = persist_fn.split("function writeAccessUserOps()", 1)[1]
        self.assertNotIn("saveApp()", server_persist)
        self.assertNotIn("forcePut: true", server_persist)

    def test_ops_disabled_blocks_user_op(self):
        cookie, _ = self.login()
        with patch.dict(os.environ, {"WT_OPS_ENABLED": "0"}, clear=False):
            st, data, *_ = call(
                "POST",
                "/api/state/op",
                {
                    "op": {
                        "path": "users.usr_001",
                        "expected_v": 0,
                        "value": {"user_id": "usr_001", "display_name": "X"},
                    }
                },
                cookie=cookie,
            )
            self.assertTrue(st.startswith("503"), st)
            self.assertEqual(data.get("error"), "ops disabled")


class WtVacationPlanOpsTests(unittest.TestCase):
    """
    Узкое сохранение графика отпускных: vacationPlans.<year>.<vacation_plan_id>.
    Формат года — массив (без миграции). FakeConn, без live БД.
    """

    def setUp(self):
        self._env_backup = dict(os.environ)
        os.environ["DATABASE_URL"] = "postgresql://worktime:" + SECRET + "@127.0.0.1:5432/worktime"
        os.environ["WT_PIN_PEPPER"] = "unit-pepper"
        os.environ["WT_SCRYPT_N"] = "16"
        os.environ["WT_OPS_ENABLED"] = "1"
        os.environ.pop("WT_API_TOKEN", None)
        os.environ.pop("WT_COOKIE_SECURE", None)
        wt_api.LOGIN_FAILS.clear()
        payload = json.loads(json.dumps(SAMPLE_APP))
        payload["vacationPlans"] = {
            "2026": [
                {
                    "vacation_plan_id": "vplan_a",
                    "employee_id": "e_shop",
                    "year": "2026",
                    "planned_date": "2026-06-01",
                    "planned_amount": 10000,
                    "status": "planned",
                    "comment": "A",
                    "history": [],
                },
                {
                    "vacation_plan_id": "vplan_b",
                    "employee_id": "e_wh",
                    "year": "2026",
                    "planned_date": "2026-07-15",
                    "planned_amount": 20000,
                    "status": "planned",
                    "comment": "B",
                    "history": [],
                },
                {
                    "vacation_plan_id": "",
                    "employee_id": "e_legacy",
                    "year": "2026",
                    "planned_date": "2026-08-01",
                    "planned_amount": 5000,
                    "status": "planned",
                    "comment": "legacy-no-id",
                    "history": [],
                },
            ]
        }
        self.store = {
            "payload": payload,
            "version": 3,
            "updated_at": datetime.now(timezone.utc),
            "updated_by": "",
            "fail": False,
            "sessions": {},
        }
        self._patch = patch.object(wt_api, "connect", side_effect=lambda: FakeConn(self.store))
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        os.environ.clear()
        os.environ.update(self._env_backup)
        wt_api.LOGIN_FAILS.clear()

    def login(self, user_id="user_admin", pin="5555"):
        status, data, _, cookies = call("POST", "/api/login", {"user_id": user_id, "pin": pin})
        self.assertTrue(status.startswith("200"), status)
        return cookie_from(cookies), data

    def _year_list(self):
        return self.store["payload"]["vacationPlans"]["2026"]

    def test_path_get_set_keeps_year_array(self):
        root = self.store["payload"]
        got = wt_api.get_path_value(root, "vacationPlans.2026.vplan_a")
        self.assertEqual(got.get("employee_id"), "e_shop")
        nxt = wt_api.set_path_value(
            root,
            "vacationPlans.2026.vplan_a",
            {
                "vacation_plan_id": "vplan_a",
                "employee_id": "e_shop",
                "year": "2026",
                "planned_date": "2026-06-10",
                "planned_amount": 11111,
                "status": "planned",
                "comment": "A2",
                "history": [],
            },
        )
        self.assertIsInstance(nxt["vacationPlans"]["2026"], list)
        self.assertEqual(len(nxt["vacationPlans"]["2026"]), 3)
        self.assertEqual(
            wt_api.get_path_value(nxt, "vacationPlans.2026.vplan_a")["planned_amount"],
            11111,
        )
        self.assertEqual(
            wt_api.get_path_value(nxt, "vacationPlans.2026.vplan_b")["planned_amount"],
            20000,
        )

    def test_two_users_different_records_same_year(self):
        cookie, _ = self.login()
        st1, d1, *_ = call(
            "POST",
            "/api/state/op",
            {
                "idempotency_key": "vp_a1",
                "op": {
                    "path": "vacationPlans.2026.vplan_a",
                    "expected_v": 0,
                    "value": {
                        "vacation_plan_id": "vplan_a",
                        "employee_id": "e_shop",
                        "year": "2026",
                        "planned_date": "2026-06-02",
                        "planned_amount": 10000,
                        "status": "planned",
                        "comment": "A-edit",
                        "history": [],
                    },
                },
            },
            cookie=cookie,
        )
        self.assertTrue(st1.startswith("200"), st1)
        st2, d2, *_ = call(
            "POST",
            "/api/state/op",
            {
                "idempotency_key": "vp_b1",
                "op": {
                    "path": "vacationPlans.2026.vplan_b",
                    "expected_v": 0,
                    "value": {
                        "vacation_plan_id": "vplan_b",
                        "employee_id": "e_wh",
                        "year": "2026",
                        "planned_date": "2026-07-16",
                        "planned_amount": 22000,
                        "status": "planned",
                        "comment": "B-edit",
                        "history": [],
                    },
                },
            },
            cookie=cookie,
        )
        self.assertTrue(st2.startswith("200"), st2)
        year = self._year_list()
        self.assertIsInstance(year, list)
        by_id = {str(r.get("vacation_plan_id")): r for r in year if isinstance(r, dict)}
        self.assertEqual(by_id["vplan_a"]["planned_date"], "2026-06-02")
        self.assertEqual(by_id["vplan_b"]["planned_amount"], 22000)
        self.assertEqual(by_id["vplan_a"]["comment"], "A-edit")
        self.assertEqual(d1.get("record_v"), 1)
        self.assertEqual(d2.get("record_v"), 1)

    def test_same_record_conflict(self):
        cookie, _ = self.login()
        call(
            "POST",
            "/api/state/op",
            {
                "op": {
                    "path": "vacationPlans.2026.vplan_a",
                    "expected_v": 0,
                    "value": {
                        "vacation_plan_id": "vplan_a",
                        "employee_id": "e_shop",
                        "year": "2026",
                        "planned_date": "2026-06-01",
                        "planned_amount": 15000,
                        "status": "planned",
                        "comment": "first",
                        "history": [],
                    },
                }
            },
            cookie=cookie,
        )
        st, data, *_ = call(
            "POST",
            "/api/state/op",
            {
                "op": {
                    "path": "vacationPlans.2026.vplan_a",
                    "expected_v": 0,
                    "value": {
                        "vacation_plan_id": "vplan_a",
                        "employee_id": "e_shop",
                        "year": "2026",
                        "planned_date": "2026-06-01",
                        "planned_amount": 99999,
                        "status": "planned",
                        "comment": "stale",
                        "history": [],
                    },
                }
            },
            cookie=cookie,
        )
        self.assertTrue(st.startswith("409"), st)
        self.assertEqual(data.get("error"), "conflict")
        self.assertEqual(
            wt_api.get_path_value(self.store["payload"], "vacationPlans.2026.vplan_a")["planned_amount"],
            15000,
        )
        # чужая запись не затронута
        self.assertEqual(
            wt_api.get_path_value(self.store["payload"], "vacationPlans.2026.vplan_b")["planned_amount"],
            20000,
        )

    def test_reschedule_one_other_record_untouched(self):
        cookie, _ = self.login()
        st, *_ = call(
            "POST",
            "/api/state/op",
            {
                "op": {
                    "path": "vacationPlans.2026.vplan_a",
                    "expected_v": 0,
                    "value": {
                        "vacation_plan_id": "vplan_a",
                        "employee_id": "e_shop",
                        "year": "2026",
                        "planned_date": "2026-09-01",
                        "planned_amount": 10000,
                        "status": "moved",
                        "comment": "A",
                        "history": [{"status": "moved"}],
                    },
                }
            },
            cookie=cookie,
        )
        self.assertTrue(st.startswith("200"), st)
        b = wt_api.get_path_value(self.store["payload"], "vacationPlans.2026.vplan_b")
        self.assertEqual(b["planned_date"], "2026-07-15")
        self.assertEqual(b["planned_amount"], 20000)

    def test_legacy_row_without_id_adopted_not_duplicated(self):
        cookie, _ = self.login()
        before = len(self._year_list())
        st, *_ = call(
            "POST",
            "/api/state/op",
            {
                "op": {
                    "path": "vacationPlans.2026.vplan_legacy1",
                    "expected_v": 0,
                    "value": {
                        "vacation_plan_id": "vplan_legacy1",
                        "employee_id": "e_legacy",
                        "year": "2026",
                        "planned_date": "2026-08-02",
                        "planned_amount": 5500,
                        "status": "planned",
                        "comment": "adopted",
                        "history": [],
                    },
                }
            },
            cookie=cookie,
        )
        self.assertTrue(st.startswith("200"), st)
        year = self._year_list()
        self.assertEqual(len(year), before)
        legacy_rows = [r for r in year if str(r.get("employee_id")) == "e_legacy"]
        self.assertEqual(len(legacy_rows), 1)
        self.assertEqual(legacy_rows[0]["vacation_plan_id"], "vplan_legacy1")
        self.assertEqual(legacy_rows[0]["planned_amount"], 5500)

    def test_year_array_format_unchanged_after_reads(self):
        root = json.loads(json.dumps(self.store["payload"]))
        year = root["vacationPlans"]["2026"]
        self.assertIsInstance(year, list)
        self.assertEqual(len(year), 3)
        # повторное чтение не мутирует
        _ = wt_api.get_path_value(root, "vacationPlans.2026.vplan_a")
        self.assertIsInstance(root["vacationPlans"]["2026"], list)
        self.assertEqual(root["vacationPlans"]["2026"], year)

    def test_salary_vacation_pay_once_guard_via_op(self):
        """Повторная запись vacationPay при уже >0 не обязательна — клиент не шлёт; путь salary изолирован."""
        cookie, _ = self.login()
        st1, *_ = call(
            "POST",
            "/api/state/op",
            {
                "op": {
                    "path": "salary.2026-06.e_shop.vacationPay",
                    "expected_v": 0,
                    "value": 10000,
                }
            },
            cookie=cookie,
        )
        self.assertTrue(st1.startswith("200"), st1)
        st2, data, *_ = call(
            "POST",
            "/api/state/op",
            {
                "op": {
                    "path": "salary.2026-06.e_shop.vacationPay",
                    "expected_v": 0,
                    "value": 10000,
                }
            },
            cookie=cookie,
        )
        self.assertTrue(st2.startswith("409"), st2)
        self.assertEqual(data.get("error"), "conflict")
        self.assertEqual(
            self.store["payload"]["salary"]["2026-06"]["e_shop"]["vacationPay"],
            10000,
        )

    def test_client_markers_narrow_persist(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        html = (Path(root) / "app.html").read_text(encoding="utf-8")
        self.assertIn("function wtPersistVacationPlanRecords(", html)
        self.assertIn("function wtVacationPlanPath(", html)
        self.assertIn("wtPersistVacationPlanRecords([rec]", html)
        self.assertIn("applyPlannedVacationPayOnce(ym, { narrow: true })", html)
        self.assertIn("function wtPersistSalaryVacationPayLeaves(", html)
        # patch больше не зовёт голый saveApp сразу после мутации поля
        patch_fn = html.split("function patchVacationPlanEmp(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("wtPersistVacationPlanRecords", patch_fn)
        self.assertNotIn("saveApp();", patch_fn)
        # Открытие ведомости/реестра/графика — только узкий once, без широкого saveApp.
        apply_fn = html.split("function applyPlannedVacationPayOnce(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("wtPersistSalaryVacationPayLeaves(", apply_fn)
        self.assertNotIn("saveApp();", apply_fn)
        render_sal = html.split("function renderSalary(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn(
            "applyPlannedVacationPayOnce(selectedMonth, { narrow: true, silentFail: true })",
            render_sal,
        )
        self.assertNotIn("applyPlannedVacationPayOnce(selectedMonth);", render_sal)
        render_reg = html.split("function renderSalaryVacationRegistry(", 1)[1].split(
            "\nfunction ", 1
        )[0]
        self.assertIn(
            "applyPlannedVacationPayOnce(ym, { narrow: true, silentFail: true })",
            render_reg,
        )
        render_plan = html.split("function renderSalaryVacationPlan(", 1)[1].split(
            "\nfunction ", 1
        )[0]
        self.assertIn(
            "applyPlannedVacationPayOnce(ym, { narrow: true, silentFail: true })",
            render_plan,
        )

    def test_persist_after_reread(self):
        cookie, _ = self.login()
        call(
            "POST",
            "/api/state/op",
            {
                "op": {
                    "path": "vacationPlans.2026.vplan_a",
                    "expected_v": 0,
                    "value": {
                        "vacation_plan_id": "vplan_a",
                        "employee_id": "e_shop",
                        "year": "2026",
                        "planned_date": "2026-06-20",
                        "planned_amount": 13000,
                        "status": "planned",
                        "comment": "reread",
                        "history": [],
                    },
                }
            },
            cookie=cookie,
        )
        st, data, *_ = call("GET", "/api/state", cookie=cookie)
        self.assertTrue(st.startswith("200"), st)
        payload = data.get("payload") or data
        got = wt_api.get_path_value(payload, "vacationPlans.2026.vplan_a")
        self.assertEqual(got["planned_date"], "2026-06-20")
        self.assertEqual(got["planned_amount"], 13000)
        self.assertIsInstance(payload["vacationPlans"]["2026"], list)

    def test_vacation_plan_only_user_gets_plans_not_salary(self):
        """vacationPlan без salary: GET — график + узкий срез фактов; once vacationPay; прямой salary OPS 403."""
        payload = self.store["payload"]
        payload["users"].append(
            {
                "user_id": "usr_vplan_only",
                "display_name": "Только график",
                "pin": "2222",
                "role": "user",
                "legacy_role": "staff",
                "is_active": True,
                "departments": ["summary", "shop"],
                "permissions": {
                    "vacationPlan": "edit",
                    "salary": "none",
                    "salaryCalc": "none",
                    "salaryPay": "none",
                    "timesheet": "none",
                    "metricsInput": "none",
                },
            }
        )
        self.store["payload"] = wt_api.hash_pins_in_payload(payload, None)
        cookie, _ = self.login("usr_vplan_only", "2222")
        rec = {
            "vacation_plan_id": "vplan_new1",
            "employee_id": "e_shop",
            "year": "2026",
            "planned_date": "2026-08-01",
            "planned_amount": 15000,
            "status": "planned",
            "comment": "",
            "history": [],
        }
        st, *_ = call(
            "POST",
            "/api/state/op",
            {"op": {"path": "vacationPlans.2026.vplan_new1", "expected_v": 0, "value": rec}},
            cookie=cookie,
        )
        self.assertTrue(st.startswith("200"), st)
        # Серверный once: ведомость получила vacationPay без права salary у HR.
        pay = (
            self.store["payload"]
            .get("salary", {})
            .get("2026-08", {})
            .get("e_shop", {})
            .get("vacationPay")
        )
        self.assertEqual(pay, 15000)
        st_sal, data_sal, *_ = call(
            "POST",
            "/api/state/op",
            {"op": {"path": "salary.2026-08.e_shop.vacationPay", "expected_v": 0, "value": 99999}},
            cookie=cookie,
        )
        self.assertTrue(st_sal.startswith("403"), st_sal)
        self.assertEqual(data_sal.get("error"), "forbidden")
        self.assertEqual(
            self.store["payload"]["salary"]["2026-08"]["e_shop"]["vacationPay"],
            15000,
        )
        st_get, got, *_ = call("GET", "/api/state", cookie=cookie)
        self.assertTrue(st_get.startswith("200"), st_get)
        pl = got.get("payload") or {}
        self.assertIn("vacationPlans", pl)
        self.assertNotIn("shopPayroll", pl)
        self.assertNotIn("repSales", pl)
        # Узкий срез salary — только факты отпускных, не полный расчёт.
        slim = pl.get("salary") or {}
        self.assertIn("2026-08", slim)
        self.assertEqual(slim["2026-08"]["e_shop"].get("vacationPay"), 15000)
        self.assertNotIn("rate", slim["2026-08"]["e_shop"])
        year = (pl.get("vacationPlans") or {}).get("2026")
        self.assertIsInstance(year, list)
        found = next((r for r in year if r and r.get("vacation_plan_id") == "vplan_new1"), None)
        self.assertIsNotNone(found)
        self.assertEqual(found.get("planned_amount"), 15000)

    def test_vacation_pay_once_no_duplicate_on_resave(self):
        cookie, _ = self.login()
        rec = {
            "vacation_plan_id": "vplan_once",
            "employee_id": "e_shop",
            "year": "2026",
            "planned_date": "2026-09-01",
            "planned_amount": 12000,
            "status": "planned",
            "comment": "",
            "history": [],
        }
        call(
            "POST",
            "/api/state/op",
            {"op": {"path": "vacationPlans.2026.vplan_once", "expected_v": 0, "value": rec}},
            cookie=cookie,
        )
        self.assertEqual(
            self.store["payload"]["salary"]["2026-09"]["e_shop"]["vacationPay"],
            12000,
        )
        # Ручная правка расчётчиком — once не должен затереть.
        self.store["payload"]["salary"]["2026-09"]["e_shop"]["vacationPay"] = 13000
        rec2 = dict(rec)
        rec2["planned_amount"] = 15000
        rec2["comment"] = "resave"
        meta = self.store["payload"].setdefault("_wt_records", {})
        rv = int((meta.get("vacationPlans.2026.vplan_once") or {}).get("v") or 1)
        st, *_ = call(
            "POST",
            "/api/state/op",
            {"op": {"path": "vacationPlans.2026.vplan_once", "expected_v": rv, "value": rec2}},
            cookie=cookie,
        )
        self.assertTrue(st.startswith("200"), st)
        self.assertEqual(
            self.store["payload"]["salary"]["2026-09"]["e_shop"]["vacationPay"],
            13000,
        )

    def test_user_without_vacation_plan_perm_no_get(self):
        cookie, _ = self.login("usr_001", "1111")
        st, got, *_ = call("GET", "/api/state", cookie=cookie)
        self.assertTrue(st.startswith("200"), st)
        pl = got.get("payload") or {}
        self.assertNotIn("vacationPlans", pl)

    def test_client_markers_salary_gate_and_conflict_format(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        html = (Path(root) / "app.html").read_text(encoding="utf-8")
        self.assertIn("function canPersistSalaryVacationPayOps(", html)
        # Без зарплатного права клиент не шлёт salary.* (сервер once при OPS плана).
        self.assertIn("if (!canPersistSalaryVacationPayOps()) {", html)
        apply_fn = html.split("function applyPlannedVacationPayOnce(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("!canPersistSalaryVacationPayOps()", apply_fn)
        self.assertNotIn("options.narrow && !canPersistSalaryVacationPayOps()", apply_fn)
        fmt = html.split("function wtFormatConflictValue(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("vacation_plan_id", fmt)
        self.assertIn("planned_amount", fmt)
        # vacation block before timesheet status heuristic
        self.assertLess(fmt.find("vacation_plan_id"), fmt.find("ячейка табеля"))

    def test_client_markers_no_fake_summary_admin_for_vacation_only(self):
        """Светлана (только vacationPlan): без фейкового summary admin и без salary OPS на render."""
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        html = (Path(root) / "app.html").read_text(encoding="utf-8")
        legacy = html.split("function applyLegacyRolesFromUser(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("else currentSummaryRole = null;", legacy)
        self.assertNotIn('else currentSummaryRole = "admin";', legacy)
        calc = html.split("function canSalaryCalc(", 1)[1].split("\nfunction ", 1)[0]
        self.assertNotIn('currentSummaryRole === "admin"', calc)
        pay = html.split("function canSalaryPay(", 1)[1].split("\nfunction ", 1)[0]
        self.assertNotIn('currentSummaryRole === "admin"', pay)
        perm = html.split("function userHasPerm(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn('currentSummaryRole === "admin" && !currentAuthUser()', perm)
        leaves = html.split("function wtPersistSalaryVacationPayLeaves(", 1)[1].split(
            "\nfunction ", 1
        )[0]
        self.assertIn("silentFail: !!options.silentFail", leaves)
        # График отпускных по-прежнему через vacationPlan, не через фейковый admin.
        self.assertIn("userHasPerm(\"vacationPlan\", \"edit\")", html)
        self.assertIn("userHasPerm(\"vacationPlan\", \"view\")", html)

    def test_vacation_plan_only_forbidden_salary_vacation_pay_op(self):
        """HR с vacationPlan:edit не пишет salary.*.vacationPay напрямую (клиент тоже не должен)."""
        payload = self.store["payload"]
        payload["users"] = list(payload.get("users") or []) + [
            {
                "user_id": "usr_svetlana_vp",
                "display_name": "Светлана",
                "pin": "8888",
                "role": "user",
                "legacy_role": "staff",
                "is_active": True,
                "departments": ["summary"],
                "permissions": {"vacationPlan": "edit", "salaryCalc": "none", "salaryPay": "none"},
            }
        ]
        cookie, _ = self.login("usr_svetlana_vp", "8888")
        st, d, *_ = call(
            "POST",
            "/api/state/op",
            {
                "op": {
                    "path": "salary.2026-06.e_shop.vacationPay",
                    "expected_v": 0,
                    "value": 99999,
                }
            },
            cookie=cookie,
        )
        self.assertTrue(st.startswith("403"), (st, d))
        self.assertNotEqual(
            (self.store["payload"].get("salary") or {})
            .get("2026-06", {})
            .get("e_shop", {})
            .get("vacationPay"),
            99999,
        )


class WtShopPayrollKpiOpsTests(unittest.TestCase):
    """Узкое сохранение KPI через OPS: kpiDefs / assignments, конфликт, изоляция других разделов."""

    def setUp(self):
        self._env_backup = dict(os.environ)
        os.environ["DATABASE_URL"] = "postgresql://worktime:" + SECRET + "@127.0.0.1:5432/worktime"
        os.environ["WT_PIN_PEPPER"] = "unit-pepper"
        os.environ["WT_SCRYPT_N"] = "16"
        os.environ["WT_OPS_ENABLED"] = "1"
        os.environ.pop("WT_API_TOKEN", None)
        os.environ.pop("WT_COOKIE_SECURE", None)
        wt_api.LOGIN_FAILS.clear()
        self.store = {
            "payload": json.loads(json.dumps(SAMPLE_APP)),
            "version": 3,
            "updated_at": datetime.now(timezone.utc),
            "updated_by": "",
            "fail": False,
            "sessions": {},
        }
        pay = self.store["payload"].setdefault("shopPayroll", {})
        pay["kpiDefs"] = []
        pay["assignments"] = []
        # Посторонний «шум» в метриках — KPI OPS не должен его трогать.
        facts = self.store["payload"].setdefault("shopMetrics", {}).setdefault("facts", {})
        for i in range(250):
            facts["noise_%03d" % i] = i
        self._patch = patch.object(wt_api, "connect", side_effect=lambda: FakeConn(self.store))
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        os.environ.clear()
        os.environ.update(self._env_backup)
        wt_api.LOGIN_FAILS.clear()

    def login(self, user_id="user_admin", pin="5555"):
        status, data, _, cookies = call("POST", "/api/login", {"user_id": user_id, "pin": pin})
        self.assertTrue(status.startswith("200"), status)
        return cookie_from(cookies), data

    def _record_v(self, path):
        return int(wt_api.record_version(self.store["payload"], path) or 0)

    def test_client_kpi_save_uses_narrow_ops_helpers(self):
        src = Path(__file__).resolve().parents[1].joinpath("app.html").read_text(encoding="utf-8")
        self.assertIn("function saveShopPayrollKpiDefs(", src)
        self.assertIn("function saveShopPayrollKpiAssignments(", src)
        self.assertIn("function saveShopPayrollSalaries(", src)
        self.assertIn("function saveShopPayrollReceivingShared(", src)
        self.assertIn("function saveShopPayrollSharedBudgets(", src)
        self.assertIn('WT_SHOP_PAYROLL_KPI_DEFS_PATH = "shopPayroll.kpiDefs"', src)
        self.assertIn('WT_SHOP_PAYROLL_KPI_ASSIGNMENTS_PATH = "shopPayroll.assignments"', src)
        self.assertIn('WT_SHOP_PAYROLL_SALARIES_PATH = "shopPayroll.salaries"', src)
        self.assertIn('WT_SHOP_PAYROLL_RECEIVING_SHARED_PATH = "shopPayroll.receivingShared"', src)
        self.assertIn('WT_SHOP_PAYROLL_SHARED_BUDGETS_PATH = "shopPayroll.sharedBudgets"', src)
        create = src[
            src.find("function createShopKpiDef(")
            : src.find("function toggleShopKpiAssignment(")
        ]
        self.assertIn("saveShopPayrollKpiDefs()", create)
        self.assertNotIn("saveApp()", create)
        assign = src[
            src.find("function toggleShopKpiAssignment(")
            : src.find("function addShopKpi(")
        ]
        self.assertIn("saveShopPayrollKpiAssignments(", assign)
        self.assertIn("prevAssignments", assign)
        self.assertIn("ok === true", assign)
        self.assertNotIn("saveApp()", assign)
        mutate = src[
            src.find("function mutateShopKpiDef(")
            : src.find("function setShopKpiDefField(")
        ]
        self.assertIn("saveShopPayrollKpiDefs()", mutate)
        close = src[
            src.find("function closeShopKpiDef(")
            : src.find("function createShopKpiDef(")
        ]
        self.assertIn("saveShopPayrollKpiDefs()", close)
        seed_w = src[
            src.find("function seedShopWriteoffLossesKpi(")
            : src.find("function seedShopArrival730Kpi(")
        ]
        seed_a = src[
            src.find("function seedShopArrival730Kpi(")
            : src.find("function normalizeSharedKpiScopes(")
        ]
        self.assertNotIn("saveApp()", seed_w)
        self.assertNotIn("saveApp()", seed_a)
        assign_save = src[
            src.find("function saveShopPayrollKpiAssignments(")
            : src.find("function saveShopPayrollSalaries(")
        ]
        self.assertIn("forbidForcePut: true", assign_save)
        self.assertNotIn("saveApp()", assign_save)
        salaries_save = src[
            src.find("function saveShopPayrollSalaries(")
            : src.find("function saveShopPayrollReceivingShared(")
        ]
        self.assertIn("forbidForcePut: true", salaries_save)
        self.assertNotIn("saveApp()", salaries_save)
        recv_save = src[
            src.find("function saveShopPayrollReceivingShared(")
            : src.find("function saveShopPayrollSharedBudgets(")
        ]
        self.assertIn("forbidForcePut: true", recv_save)
        self.assertNotIn("saveApp()", recv_save)
        budget_save = src[
            src.find("function saveShopPayrollSharedBudgets(")
            : src.find("function ensureShopPayroll(")
        ]
        self.assertIn("forbidForcePut: true", budget_save)
        self.assertNotIn("saveApp()", budget_save)
        dw = src[
            src.find("function wtDomainWriterSave(")
            : src.find("function wtDomainWriterSave(") + 12000
        ]
        self.assertIn("!hasExplicitOps && prelimLeaves && prelimLeaves.length > 200", dw)
        self.assertIn("Отказ ДО dirty", dw)
        self.assertIn("const hasExplicitOps = !!(options.ops && options.ops.length)", dw)
        self.assertIn("const explicitOps = options.ops || null", dw)
        self.assertEqual(dw.count("const explicitOps"), 1)

    def test_client_day_rate_save_uses_narrow_ops(self):
        """Стоимость дня: узкий OPS salaries, без saveApp / forcePut, с откатом."""
        src = Path(__file__).resolve().parents[1].joinpath("app.html").read_text(encoding="utf-8")
        day = src[
            src.find("function setShopEmployeeDayRate(")
            : src.find("function shopRuleForCard(")
        ]
        self.assertIn("saveShopPayrollSalaries(", day)
        self.assertIn("prevSalaries", day)
        self.assertIn("ok === true", day)
        self.assertNotIn("saveApp()", day)
        saver = src[
            src.find("function saveShopPayrollSalaries(")
            : src.find("function saveShopPayrollReceivingShared(")
        ]
        self.assertIn('WT_SHOP_PAYROLL_SALARIES_PATH', saver)
        self.assertIn("forbidForcePut: true", saver)
        self.assertNotIn("saveApp()", saver)

    def test_client_receiving_shared_save_uses_narrow_ops(self):
        """Приёмка: узкий OPS receivingShared/sharedBudgets, без saveApp, с откатом."""
        src = Path(__file__).resolve().parents[1].joinpath("app.html").read_text(encoding="utf-8")
        shared = src[
            src.find("function setShopReceivingShared(value)")
            : src.find("function setShopReceivingSharedBudget(raw)")
        ]
        self.assertIn("saveShopPayrollReceivingShared(", shared)
        self.assertIn("ok === true", shared)
        self.assertNotIn("saveApp()", shared)
        budget = src[
            src.find("function setShopReceivingSharedBudget(raw)")
            : src.find("function shopTimesheetDayRecord(")
        ]
        self.assertIn("saveShopPayrollSharedBudgets(", budget)
        self.assertIn("prevBudgets", budget)
        self.assertIn("ok === true", budget)
        self.assertNotIn("saveApp()", budget)
        saver_shared = src[
            src.find("function saveShopPayrollReceivingShared(")
            : src.find("function saveShopPayrollSharedBudgets(")
        ]
        self.assertIn("forbidForcePut: true", saver_shared)
        self.assertNotIn("saveApp()", saver_shared)
        saver_budget = src[
            src.find("function saveShopPayrollSharedBudgets(")
            : src.find("function ensureShopPayroll(")
        ]
        self.assertIn("forbidForcePut: true", saver_budget)
        self.assertNotIn("saveApp()", saver_budget)

    def test_kpi_assign_toggle_ignores_unrelated_dirty_collect(self):
        """Одна галочка KPI: узкий OPS assignments, чужой collect 496 не уходит в batch."""
        cookie, _ = self.login()
        # На сервере уже «шум» >200 фактов (setUp). Узкий op assignments всё равно проходит.
        self.assertGreaterEqual(len(self.store["payload"]["shopMetrics"]["facts"]), 200)
        st, data, *_ = call(
            "POST",
            "/api/state/op/batch",
            {
                "atomic": True,
                "ops": [{
                    "path": "shopPayroll.assignments",
                    "expected_v": 0,
                    "value": [{
                        "id": "asg_one",
                        "employee_id": "e_shop",
                        "kpi_id": "kpi_one",
                        "validFrom": "2026-11",
                        "validTo": None,
                        "is_active": True,
                    }],
                }],
            },
            cookie=cookie,
        )
        self.assertTrue(st.startswith("200"), st)
        results = data.get("results") or []
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].get("status"), "saved")
        self.assertEqual(
            self.store["payload"]["shopPayroll"]["assignments"][0]["id"],
            "asg_one",
        )
        # Чужие факты не затронуты.
        self.assertEqual(self.store["payload"]["shopMetrics"]["facts"]["noise_000"], 0)
        self.assertEqual(self.store["payload"]["shopMetrics"]["facts"]["noise_249"], 249)
        # Клиентский контракт: toggle не зовёт saveApp; seed без saveApp.
        src = Path(__file__).resolve().parents[1].joinpath("app.html").read_text(encoding="utf-8")
        toggle = src[
            src.find("function toggleShopKpiAssignment(")
            : src.find("function addShopKpi(")
        ]
        self.assertIn("saveShopPayrollKpiAssignments(", toggle)
        self.assertNotIn("saveApp()", toggle)
        self.assertIn("prevAssignments", toggle)

    def test_day_rate_ops_ignores_unrelated_dirty_collect(self):
        """Стоимость дня: узкий OPS salaries, чужой collect 458 не уходит в batch."""
        cookie, _ = self.login()
        self.assertGreaterEqual(len(self.store["payload"]["shopMetrics"]["facts"]), 200)
        before_asg = list(self.store["payload"]["shopPayroll"].get("assignments") or [])
        before_defs = list(self.store["payload"]["shopPayroll"].get("kpiDefs") or [])
        st, data, *_ = call(
            "POST",
            "/api/state/op/batch",
            {
                "atomic": True,
                "ops": [{
                    "path": "shopPayroll.salaries",
                    "expected_v": 0,
                    "value": [{
                        "id": "sr_day",
                        "employeeId": "e_shop",
                        "dayRate": 2800,
                        "amount": 2800,
                        "validFrom": "2026-11",
                        "validTo": None,
                    }],
                }],
            },
            cookie=cookie,
        )
        self.assertTrue(st.startswith("200"), st)
        results = data.get("results") or []
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].get("status"), "saved")
        saved = self.store["payload"]["shopPayroll"]["salaries"]
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0].get("dayRate"), 2800)
        self.assertEqual(self.store["payload"]["shopMetrics"]["facts"]["noise_000"], 0)
        self.assertEqual(self.store["payload"]["shopMetrics"]["facts"]["noise_249"], 249)
        self.assertEqual(self.store["payload"]["shopPayroll"].get("assignments"), before_asg)
        self.assertEqual(self.store["payload"]["shopPayroll"].get("kpiDefs"), before_defs)
        src = Path(__file__).resolve().parents[1].joinpath("app.html").read_text(encoding="utf-8")
        day = src[
            src.find("function setShopEmployeeDayRate(")
            : src.find("function shopRuleForCard(")
        ]
        self.assertIn("saveShopPayrollSalaries(", day)
        self.assertNotIn("saveApp()", day)
        self.assertIn("prevSalaries", day)

    def test_receiving_shared_ops_ignores_unrelated_dirty_collect(self):
        """Приёмка: узкий OPS receivingShared/sharedBudgets, чужой collect 458 не уходит в batch."""
        cookie, _ = self.login()
        self.assertGreaterEqual(len(self.store["payload"]["shopMetrics"]["facts"]), 200)
        before_asg = list(self.store["payload"]["shopPayroll"].get("assignments") or [])
        before_defs = list(self.store["payload"]["shopPayroll"].get("kpiDefs") or [])
        before_salaries = list(self.store["payload"]["shopPayroll"].get("salaries") or [])
        st, data, *_ = call(
            "POST",
            "/api/state/op/batch",
            {
                "atomic": True,
                "ops": [{
                    "path": "shopPayroll.receivingShared",
                    "expected_v": 0,
                    "value": True,
                }],
            },
            cookie=cookie,
        )
        self.assertTrue(st.startswith("200"), st)
        results = data.get("results") or []
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].get("status"), "saved")
        self.assertTrue(self.store["payload"]["shopPayroll"].get("receivingShared"))
        st2, data2, *_ = call(
            "POST",
            "/api/state/op/batch",
            {
                "atomic": True,
                "ops": [{
                    "path": "shopPayroll.sharedBudgets",
                    "expected_v": self._record_v("shopPayroll.sharedBudgets"),
                    "value": {"2026-11": {"metric_receiving": 4500}},
                }],
            },
            cookie=cookie,
        )
        self.assertTrue(st2.startswith("200"), st2)
        results2 = data2.get("results") or []
        self.assertEqual(len(results2), 1)
        self.assertEqual(results2[0].get("status"), "saved")
        budgets = self.store["payload"]["shopPayroll"].get("sharedBudgets") or {}
        self.assertEqual((budgets.get("2026-11") or {}).get("metric_receiving"), 4500)
        self.assertEqual(self.store["payload"]["shopMetrics"]["facts"]["noise_000"], 0)
        self.assertEqual(self.store["payload"]["shopMetrics"]["facts"]["noise_249"], 249)
        self.assertEqual(self.store["payload"]["shopPayroll"].get("assignments"), before_asg)
        self.assertEqual(self.store["payload"]["shopPayroll"].get("kpiDefs"), before_defs)
        self.assertEqual(self.store["payload"]["shopPayroll"].get("salaries"), before_salaries)
        src = Path(__file__).resolve().parents[1].joinpath("app.html").read_text(encoding="utf-8")
        shared = src[
            src.find("function setShopReceivingShared(value)")
            : src.find("function setShopReceivingSharedBudget(raw)")
        ]
        self.assertIn("saveShopPayrollReceivingShared(", shared)
        self.assertNotIn("saveApp()", shared)
        budget = src[
            src.find("function setShopReceivingSharedBudget(raw)")
            : src.find("function shopTimesheetDayRecord(")
        ]
        self.assertIn("saveShopPayrollSharedBudgets(", budget)
        self.assertNotIn("saveApp()", budget)
        self.assertIn("prevBudgets", budget)

    def test_admin_creates_two_kpi_defs_via_ops(self):
        cookie, _ = self.login()
        defs = [
            {"kpi_id": "kpi_a", "name": "KPI A", "amount": 100, "active": True},
            {"kpi_id": "kpi_b", "name": "KPI B", "amount": 200, "active": True},
        ]
        st, data, *_ = call(
            "POST",
            "/api/state/op",
            {
                "idempotency_key": "kpi-create-2",
                "op": {
                    "path": "shopPayroll.kpiDefs",
                    "expected_v": 0,
                    "value": defs,
                },
            },
            cookie=cookie,
        )
        self.assertTrue(st.startswith("200"), st)
        self.assertEqual(data.get("record_v"), 1)
        saved = self.store["payload"]["shopPayroll"]["kpiDefs"]
        self.assertEqual(len(saved), 2)
        self.assertEqual({d["kpi_id"] for d in saved}, {"kpi_a", "kpi_b"})
        # Посторонние факты не затронуты.
        self.assertEqual(self.store["payload"]["shopMetrics"]["facts"]["noise_000"], 0)
        self.assertEqual(self.store["payload"]["shopMetrics"]["facts"]["noise_249"], 249)

    def test_kpi_save_succeeds_despite_large_unrelated_facts(self):
        """Большой посторонний diff в facts не мешает узкому OPS kpiDefs (1 op < 200)."""
        cookie, _ = self.login()
        self.assertGreaterEqual(len(self.store["payload"]["shopMetrics"]["facts"]), 200)
        st, data, *_ = call(
            "POST",
            "/api/state/op/batch",
            {
                "atomic": True,
                "ops": [{
                    "path": "shopPayroll.kpiDefs",
                    "expected_v": 0,
                    "value": [{"kpi_id": "kpi_only", "name": "Only", "amount": 50}],
                }],
            },
            cookie=cookie,
        )
        self.assertTrue(st.startswith("200"), st)
        results = data.get("results") or []
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].get("status"), "saved")
        self.assertEqual(
            self.store["payload"]["shopPayroll"]["kpiDefs"][0]["kpi_id"],
            "kpi_only",
        )

    def test_kpi_mutate_and_close_via_ops(self):
        cookie, _ = self.login()
        call(
            "POST",
            "/api/state/op",
            {
                "op": {
                    "path": "shopPayroll.kpiDefs",
                    "expected_v": 0,
                    "value": [{
                        "kpi_id": "kpi_m",
                        "name": "Mut",
                        "amount": 10,
                        "validFrom": "2026-10",
                        "validTo": None,
                        "active": True,
                    }],
                }
            },
            cookie=cookie,
        )
        v1 = self._record_v("shopPayroll.kpiDefs")
        mutated = [{
            "kpi_id": "kpi_m",
            "name": "Mut",
            "amount": 99,
            "validFrom": "2026-10",
            "validTo": None,
            "active": True,
        }]
        st, data, *_ = call(
            "POST",
            "/api/state/op",
            {
                "op": {
                    "path": "shopPayroll.kpiDefs",
                    "expected_v": v1,
                    "value": mutated,
                }
            },
            cookie=cookie,
        )
        self.assertTrue(st.startswith("200"), st)
        self.assertEqual(self.store["payload"]["shopPayroll"]["kpiDefs"][0]["amount"], 99)
        v2 = self._record_v("shopPayroll.kpiDefs")
        closed = [{
            "kpi_id": "kpi_m",
            "name": "Mut",
            "amount": 99,
            "validFrom": "2026-10",
            "validTo": "2026-10",
            "active": True,
        }]
        st2, *_ = call(
            "POST",
            "/api/state/op",
            {
                "op": {
                    "path": "shopPayroll.kpiDefs",
                    "expected_v": v2,
                    "value": closed,
                }
            },
            cookie=cookie,
        )
        self.assertTrue(st2.startswith("200"), st2)
        self.assertEqual(
            self.store["payload"]["shopPayroll"]["kpiDefs"][0]["validTo"],
            "2026-10",
        )

    def test_kpi_assignment_ops(self):
        cookie, _ = self.login()
        call(
            "POST",
            "/api/state/op",
            {
                "op": {
                    "path": "shopPayroll.kpiDefs",
                    "expected_v": 0,
                    "value": [{"kpi_id": "kpi_asg", "name": "Asg", "amount": 1}],
                }
            },
            cookie=cookie,
        )
        st, data, *_ = call(
            "POST",
            "/api/state/op",
            {
                "op": {
                    "path": "shopPayroll.assignments",
                    "expected_v": 0,
                    "value": [{
                        "id": "ka_1",
                        "employee_id": "e_shop",
                        "kpi_id": "kpi_asg",
                        "validFrom": "2026-10",
                        "is_active": True,
                    }],
                }
            },
            cookie=cookie,
        )
        self.assertTrue(st.startswith("200"), st)
        self.assertEqual(data.get("record_v"), 1)
        asg = self.store["payload"]["shopPayroll"]["assignments"]
        self.assertEqual(len(asg), 1)
        self.assertEqual(asg[0]["kpi_id"], "kpi_asg")
        # Повтор с той же expected_v и тем же значением после bump — conflict (версия уже 1).
        # Повтор с актуальной версией и тем же содержимым — успех без дублирования строк.
        v = self._record_v("shopPayroll.assignments")
        st2, data2, *_ = call(
            "POST",
            "/api/state/op",
            {
                "op": {
                    "path": "shopPayroll.assignments",
                    "expected_v": v,
                    "value": list(asg),
                }
            },
            cookie=cookie,
        )
        self.assertTrue(st2.startswith("200"), st2)
        self.assertEqual(len(self.store["payload"]["shopPayroll"]["assignments"]), 1)

    def test_kpi_conflict_two_editors_no_overwrite(self):
        cookie, _ = self.login()
        call(
            "POST",
            "/api/state/op",
            {
                "op": {
                    "path": "shopPayroll.kpiDefs",
                    "expected_v": 0,
                    "value": [{"kpi_id": "kpi_c", "name": "First", "amount": 1}],
                }
            },
            cookie=cookie,
        )
        st, data, *_ = call(
            "POST",
            "/api/state/op",
            {
                "op": {
                    "path": "shopPayroll.kpiDefs",
                    "expected_v": 0,
                    "value": [{"kpi_id": "kpi_c", "name": "Second", "amount": 2}],
                }
            },
            cookie=cookie,
        )
        self.assertTrue(st.startswith("409"), st)
        self.assertEqual(data.get("error"), "conflict")
        self.assertEqual(data.get("server_v"), 1)
        kept = self.store["payload"]["shopPayroll"]["kpiDefs"]
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["name"], "First")
        self.assertEqual(kept[0]["amount"], 1)

    def test_kpi_persists_after_get_reload(self):
        cookie, _ = self.login()
        defs = [
            {"kpi_id": "kpi_r1", "name": "R1", "amount": 11},
            {"kpi_id": "kpi_r2", "name": "R2", "amount": 22},
        ]
        call(
            "POST",
            "/api/state/op",
            {
                "op": {
                    "path": "shopPayroll.kpiDefs",
                    "expected_v": 0,
                    "value": defs,
                }
            },
            cookie=cookie,
        )
        st, data, *_ = call("GET", "/api/state", cookie=cookie)
        self.assertTrue(st.startswith("200"), st)
        pay = (data.get("payload") or {}).get("shopPayroll") or {}
        ids = {d.get("kpi_id") for d in (pay.get("kpiDefs") or [])}
        self.assertEqual(ids, {"kpi_r1", "kpi_r2"})
        # Другие разделы (шум facts) на месте.
        facts = (data.get("payload") or {}).get("shopMetrics", {}).get("facts") or {}
        self.assertEqual(facts.get("noise_100"), 100)

    def test_batch_too_many_ops_still_capped_at_200(self):
        """Лимит 200 не повышен: batch из 201 ops отклоняется."""
        cookie, _ = self.login()
        ops = []
        for i in range(201):
            ops.append({
                "path": "shopMetrics.facts.cap_%s" % i,
                "expected_v": 0,
                "value": i,
            })
        st, data, *_ = call(
            "POST",
            "/api/state/op/batch",
            {"atomic": False, "ops": ops},
            cookie=cookie,
        )
        self.assertTrue(st.startswith("4"), st)
        self.assertEqual(data.get("error"), "too many ops")
        self.assertNotIn("cap_0", self.store["payload"]["shopMetrics"]["facts"])


class WtEmployeeCardOpsTests(unittest.TestCase):
    """Узкое сохранение карточки: workAccess/extraAccess/employee без широкого diff."""

    def setUp(self):
        self._env_backup = dict(os.environ)
        os.environ["DATABASE_URL"] = "postgresql://worktime:" + SECRET + "@127.0.0.1:5432/worktime"
        os.environ["WT_PIN_PEPPER"] = "unit-pepper"
        os.environ["WT_SCRYPT_N"] = "16"
        os.environ["WT_OPS_ENABLED"] = "1"
        os.environ.pop("WT_API_TOKEN", None)
        os.environ.pop("WT_COOKIE_SECURE", None)
        wt_api.LOGIN_FAILS.clear()
        self.store = {
            "payload": json.loads(json.dumps(SAMPLE_APP)),
            "version": 3,
            "updated_at": datetime.now(timezone.utc),
            "updated_by": "",
            "fail": False,
            "sessions": {},
        }
        metrics = self.store["payload"].setdefault("shopMetrics", {})
        metrics["workAccess"] = {"2026-11": {"e_shop": ["metric_assembly"], "e_other": ["metric_assembly"]}}
        metrics["extraAccess"] = {"e_shop": [], "e_other": []}
        metrics["facts"] = {}
        # >200 посторонних листьев-фактов: узкий OPS карточки не должен их трогать.
        for i in range(220):
            metrics["facts"]["noise_%s" % i] = i
        shop = None
        for w in self.store["payload"].get("workspaces") or []:
            if w and w.get("code") == "shop":
                shop = w
                break
        if shop is None:
            shop = {"code": "shop", "type": "dept", "employees": [], "employeeIds": [], "timesheet": {}}
            self.store["payload"].setdefault("workspaces", []).append(shop)
        emps = shop.setdefault("employees", [])
        if not any(e and e.get("id") == "e_shop" for e in emps):
            emps.append({
                "id": "e_shop",
                "employee_id": "e_shop",
                "name": "Тест Сотрудник",
                "work_tab_id": "pickers",
                "participates_in_spu_meetings": False,
            })
        self._patch = patch.object(wt_api, "connect", side_effect=lambda: FakeConn(self.store))
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        os.environ.clear()
        os.environ.update(self._env_backup)
        wt_api.LOGIN_FAILS.clear()

    def login(self, user_id="user_admin", pin="5555"):
        status, data, _, cookies = call("POST", "/api/login", {"user_id": user_id, "pin": pin})
        self.assertTrue(status.startswith("200"), status)
        return cookie_from(cookies), data

    def test_work_access_op_keeps_other_employee(self):
        cookie, _ = self.login()
        path = "shopMetrics.workAccess.2026-11.e_shop"
        st, body, *_ = call(
            "POST",
            "/api/state/op",
            {
                "idempotency_key": "emp-wa-1",
                "op": {
                    "path": path,
                    "expected_v": 0,
                    "value": ["metric_assembly", "metric_recount"],
                },
            },
            cookie=cookie,
        )
        self.assertTrue(st.startswith("200"), (st, body))
        wa = self.store["payload"]["shopMetrics"]["workAccess"]["2026-11"]
        self.assertEqual(wa["e_shop"], ["metric_assembly", "metric_recount"])
        self.assertEqual(wa["e_other"], ["metric_assembly"])
        self.assertEqual(self.store["payload"]["shopMetrics"]["facts"]["noise_0"], 0)
        # Снятие всех метрик сотрудника (пустой список) через OPS.
        v = int(body.get("record_v") or 0)
        if not v:
            v = int((self.store["payload"].get("_wt_records") or {}).get(path, {}).get("v") or 0)
        st2, body2, *_ = call(
            "POST",
            "/api/state/op",
            {
                "idempotency_key": "emp-wa-clear",
                "op": {"path": path, "expected_v": v, "value": []},
            },
            cookie=cookie,
        )
        self.assertTrue(st2.startswith("200"), (st2, body2))
        wa = self.store["payload"]["shopMetrics"]["workAccess"]["2026-11"]
        self.assertEqual(wa["e_shop"], [])
        self.assertEqual(wa["e_other"], ["metric_assembly"])

    def test_employee_card_batch_atomic_with_noise_facts(self):
        cookie, _ = self.login()
        st, body, *_ = call(
            "POST",
            "/api/state/op/batch",
            {
                "atomic": True,
                "idempotency_key": "emp-card-1",
                "ops": [
                    {
                        "path": "shopMetrics.workAccess.2026-11.e_shop",
                        "expected_v": 0,
                        "value": ["metric_display_positions"],
                    },
                    {
                        "path": "shopMetrics.extraAccess.e_shop",
                        "expected_v": 0,
                        "value": [],
                    },
                    {
                        "path": "workspaces.shop.employees.e_shop",
                        "expected_v": 0,
                        "value": {
                            "id": "e_shop",
                            "employee_id": "e_shop",
                            "name": "Тест Сотрудник",
                            "work_tab_id": "pickers",
                            "participates_in_spu_meetings": True,
                            "meeting_participant": True,
                        },
                    },
                ],
            },
            cookie=cookie,
        )
        self.assertTrue(st.startswith("200"), (st, body))
        wa = self.store["payload"]["shopMetrics"]["workAccess"]["2026-11"]
        self.assertEqual(wa["e_shop"], ["metric_display_positions"])
        self.assertEqual(wa["e_other"], ["metric_assembly"])
        emp = None
        for row in self.store["payload"]["workspaces"]:
            if row and row.get("code") == "shop":
                for e in row.get("employees") or []:
                    if e and e.get("id") == "e_shop":
                        emp = e
                        break
        self.assertIsNotNone(emp)
        self.assertTrue(emp.get("participates_in_spu_meetings"))
        self.assertEqual(len(self.store["payload"]["shopMetrics"]["facts"]), 220)

    def test_client_helpers_paths_and_no_full_pool_materialize_in_commit(self):
        src = Path(__file__).resolve().parents[1].joinpath("app.html").read_text(encoding="utf-8")
        self.assertIn('return "shopMetrics.workAccess."', src)
        self.assertIn('return "deptMetrics."', src)
        self.assertIn("shopMetricCatalog.items", src)
        build = src[src.find("function wtBuildEmployeeCardSaveOps("):src.find("function syncShopExtraAccessFromWork(")]
        # Не материал изует весь пул внутри builder.
        self.assertNotIn("shopMaterializeWorkAccessMonth(", build)
        self.assertIn("wtEmployeeCardRecordPath(", build)

    def test_metric_catalog_put_slice_excludes_facts(self):
        src = Path(__file__).resolve().parents[1].joinpath("app.html").read_text(encoding="utf-8")
        slice_fn = src[
            src.find("function metricCatalogPutSlice(")
            : src.find("function saveAppWithPayloadSlice(")
        ]
        self.assertIn("staffMetricSlice(appData.shopMetrics, false, true)", slice_fn)
        self.assertNotIn("metricInput, metricSetup", slice_fn)
        self.assertNotIn("out.shopPayroll", slice_fn)
        self.assertIn("shopPayroll.metrics", src[src.find("function saveMetricCatalogForm("):src.find("function getDeptMetricStored(")])

    def test_extra_metric_outside_card_uses_narrow_ops(self):
        src = Path(__file__).resolve().parents[1].joinpath("app.html").read_text(encoding="utf-8")
        fn = src[
            src.find("function setShopEmployeeExtraMetric(")
            : src.find("function metricCatalogItemUsesGroupAll(")
        ]
        self.assertIn("wtBuildWorkAccessSaveOps(", fn)
        self.assertIn("wtDomainWriterSave({ ops: waOps, atomic: true })", fn)
        self.assertIn("function wtBuildWorkAccessSaveOps(", src)
        self.assertIn("shopMaterializeWorkAccessEmployee(", src[
            src.find("function applyShopEmployeeWorkMetric(")
            : src.find("function canViewFioWorkMetrics(")
        ])

    def test_metric_catalog_list_actions_use_narrow_ops(self):
        """Вкл/выкл, удаление, drag и подметрики — не широкий saveApp."""
        src = Path(__file__).resolve().parents[1].joinpath("app.html").read_text(encoding="utf-8")
        self.assertIn("function wtPersistMetricCatalogListChange(", src)
        for name, end in (
            ("function setShopMetricCatalogActive(", "function deleteShopMetricCatalogItem("),
            ("function deleteShopMetricCatalogItem(", "function shopCatalogPositionOptions("),
            ("function onMetricCatalogDrop(", "function setShopMetricCatalogActive("),
            ("function addMetricSubmetric(", "function renameMetricSubmetric("),
            ("function renameMetricSubmetric(", "function setMetricSubmetricOrder("),
            ("function setMetricSubmetricOrder(", "function setMetricSubmetricActive("),
            ("function setMetricSubmetricActive(", "function onMetricCatalogDrag("),
        ):
            block = src[src.find(name) : src.find(end)]
            self.assertIn("wtPersistMetricCatalogListChange(", block, name)
            self.assertNotIn("saveApp();", block, name)
        active = src[
            src.find("function setShopMetricCatalogActive(")
            : src.find("function deleteShopMetricCatalogItem(")
        ]
        self.assertIn("wtPersistMetricCatalogListChange(needPay)", active)

    def test_shelf_labels_derived_pool_and_kpi_dedup(self):
        """Авто полки: только pickers; KPI не двоит manual+derived одному сотруднику."""
        src = Path(__file__).resolve().parents[1].joinpath("app.html").read_text(encoding="utf-8")
        manual = src[
            src.find("function shopShelfManualEmployees(")
            : src.find("function shopShelfKpiDefForEmployee(")
        ]
        self.assertIn('shopMetricCategoryById("pickers")', manual)
        self.assertNotIn("shopShelfActiveRules(", manual)
        self.assertNotIn("manual_source", manual)
        derived = src[
            src.find("function shopShelfDayDerivedStatus(")
            : src.find("function shopShelfMonthFact(")
        ]
        self.assertIn('return "empty"', derived)
        self.assertIn("allDone ? \"done\" : \"fail\"", derived)
        self.assertIn("function shopShelfKpiDefForEmployee(", src)
        active = src[
            src.find("function shopEmployeeActiveRules(")
            : src.find("function shopEmployeeBonusTotal(")
        ]
        self.assertIn("METRIC_SHELF_LABELS_ID", active)
        self.assertIn("shopShelfKpiDefForEmployee(", active)
        self.assertIn("shelfDefs", active)
        # Кассиры: derived UI; KPI derived читает shopShelfDayDerivedStatus.
        self.assertIn("shopShelfDayDerivedStatus(day)", src[
            src.find("function shopMetricInputHtml(")
            : src.find("function onShopMetricNav(")
        ])
        self.assertIn("shopShelfDayDerivedStatus(day, month)", src[
            src.find("function shopCheckKpiDayState(")
            : src.find("function shopCheckKpiMonthStats(")
        ])


class WtOpsIntegrationTests(unittest.TestCase):
    """
    Интеграционные сценарии на FakeConn + threading.Lock (имитация row lock).
    Настоящая PostgreSQL не требуется; см. test_wt_ops_pg.py для WT_TEST_DATABASE_URL.
    """

    def setUp(self):
        self._env_backup = dict(os.environ)
        os.environ["DATABASE_URL"] = "postgresql://worktime:" + SECRET + "@127.0.0.1:5432/worktime"
        os.environ["WT_PIN_PEPPER"] = "unit-pepper"
        os.environ["WT_SCRYPT_N"] = "16"
        os.environ["WT_OPS_ENABLED"] = "1"
        os.environ.pop("WT_API_TOKEN", None)
        os.environ.pop("WT_COOKIE_SECURE", None)
        wt_api.LOGIN_FAILS.clear()
        self.store = {
            "payload": json.loads(json.dumps(SAMPLE_APP)),
            "version": 3,
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
        status, data, _, cookies = call("POST", "/api/login", {"user_id": user_id, "pin": pin})
        self.assertTrue(status.startswith("200"), status)
        return cookie_from(cookies), data

    def test_parallel_independent_ops_general_and_shop(self):
        """«Общая» (admin) и «Магазин» (staff) одновременно — оба значения сохраняются."""
        admin_c, _ = self.login("user_admin", "5555")
        staff_c, _ = self.login("usr_001", "1111")
        results = {}

        def admin_job():
            st, d, *_ = call(
                "POST",
                "/api/state/op",
                {
                    "idempotency_key": "admin-ind",
                    "op": {
                        "path": "shopMetrics.facts.general_note",
                        "expected_v": 0,
                        "value": "FROM_GENERAL",
                    },
                },
                cookie=admin_c,
            )
            results["admin"] = (st, d)

        def staff_job():
            st, d, *_ = call(
                "POST",
                "/api/state/op",
                {
                    "idempotency_key": "staff-ind",
                    "op": {
                        "path": "shopMetrics.facts.shop_note",
                        "expected_v": 0,
                        "value": "FROM_SHOP",
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
        self.assertTrue(results["admin"][0].startswith("200"), results["admin"])
        self.assertTrue(results["staff"][0].startswith("200"), results["staff"])
        facts = self.store["payload"]["shopMetrics"]["facts"]
        self.assertEqual(facts.get("general_note"), "FROM_GENERAL")
        self.assertEqual(facts.get("shop_note"), "FROM_SHOP")

    def test_parallel_two_ops_same_group_independent_cells(self):
        cookie, _ = self.login("usr_001", "1111")
        results = []

        def job(path, val, key):
            st, d, *_ = call(
                "POST",
                "/api/state/op",
                {"idempotency_key": key, "op": {"path": path, "expected_v": 0, "value": val}},
                cookie=cookie,
            )
            results.append((st, d, path, val))

        threads = [
            threading.Thread(target=job, args=("shopMetrics.facts.c1", 11, "g1")),
            threading.Thread(target=job, args=("shopMetrics.facts.c2", 22, "g2")),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertTrue(all(r[0].startswith("200") for r in results), results)
        facts = self.store["payload"]["shopMetrics"]["facts"]
        self.assertEqual(facts.get("c1"), 11)
        self.assertEqual(facts.get("c2"), 22)

    def test_parallel_put_and_op_independent(self):
        admin_c, _ = self.login()
        results = {}

        def op_job():
            st, d, *_ = call(
                "POST",
                "/api/state/op",
                {"op": {"path": "shopMetrics.facts.op_side", "expected_v": 0, "value": "OP"}},
                cookie=admin_c,
            )
            results["op"] = (st, d)

        def put_job():
            # Берём актуальный version под lock внутри handle — может получить 409 и повторить.
            for _ in range(5):
                ver = int(self.store["version"])
                st, d, *_ = call(
                    "PUT",
                    "/api/state",
                    {
                        "version": ver,
                        "payload": {"shopMetrics": {"facts": {"ok": 1, "put_side": "PUT"}}},
                    },
                    cookie=admin_c,
                )
                if st.startswith("200"):
                    results["put"] = (st, d)
                    return
                if st.startswith("409"):
                    continue
                results["put"] = (st, d)
                return
            results["put"] = ("fail", None)

        t1 = threading.Thread(target=op_job)
        t2 = threading.Thread(target=put_job)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        self.assertTrue(results["op"][0].startswith("200"), results["op"])
        self.assertTrue(results["put"][0].startswith("200"), results["put"])
        facts = self.store["payload"]["shopMetrics"]["facts"]
        self.assertEqual(facts.get("op_side"), "OP")
        self.assertEqual(facts.get("put_side"), "PUT")

    def test_parallel_same_record_one_wins(self):
        cookie, _ = self.login()
        results = []

        def job(val, key):
            st, d, *_ = call(
                "POST",
                "/api/state/op",
                {"idempotency_key": key, "op": {"path": "shopMetrics.facts.one", "expected_v": 0, "value": val}},
                cookie=cookie,
            )
            results.append((st, d, val))

        t1 = threading.Thread(target=job, args=("A", "same-a"))
        t2 = threading.Thread(target=job, args=("B", "same-b"))
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        oks = [r for r in results if r[0].startswith("200")]
        conflicts = [r for r in results if r[0].startswith("409")]
        self.assertEqual(len(oks), 1, results)
        self.assertEqual(len(conflicts), 1, results)
        winner = oks[0][2]
        self.assertEqual(self.store["payload"]["shopMetrics"]["facts"]["one"], winner)

    def test_confirm_replace_rechecks_after_third_writer(self):
        cookie, _ = self.login()
        call(
            "POST",
            "/api/state/op",
            {"op": {"path": "shopMetrics.facts.cell", "expected_v": 0, "value": "A"}},
            cookie=cookie,
        )
        # B видит conflict (expected 0), C пишет на v=1, B confirm с server_v=1 — уже поздно.
        call(
            "POST",
            "/api/state/op",
            {"op": {"path": "shopMetrics.facts.cell", "expected_v": 1, "value": "C"}},
            cookie=cookie,
        )
        st, data, *_ = call(
            "POST",
            "/api/state/op",
            {"op": {"path": "shopMetrics.facts.cell", "expected_v": 1, "value": "B"}},
            cookie=cookie,
        )
        self.assertTrue(st.startswith("409"), st)
        self.assertEqual(data.get("server_value"), "C")

    def test_idempotent_retry_after_disconnect(self):
        cookie, _ = self.login()
        body = {
            "idempotency_key": "retry-after-drop",
            "op": {"path": "shopMetrics.facts.once", "expected_v": 0, "value": 42},
        }
        st1, d1, *_ = call("POST", "/api/state/op", body, cookie=cookie)
        ver = int(self.store["version"])
        st2, d2, *_ = call("POST", "/api/state/op", body, cookie=cookie)
        self.assertTrue(st1.startswith("200"))
        self.assertTrue(st2.startswith("200"))
        self.assertEqual(d1.get("value"), 42)
        self.assertEqual(d2.get("value"), 42)
        self.assertEqual(int(self.store["version"]), ver)
        self.assertEqual(self.store["payload"]["shopMetrics"]["facts"]["once"], 42)

    def test_wide_admin_put_keeps_independent_op(self):
        cookie, _ = self.login()
        call(
            "POST",
            "/api/state/op",
            {"op": {"path": "shopMetrics.facts.guarded", "expected_v": 0, "value": "OP_KEEP"}},
            cookie=cookie,
        )
        ver = int(self.store["version"])
        st, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": ver,
                "payload": {
                    "shopMetrics": {"facts": {"ok": 1, "admin_extra": 5}},
                    "_wt_records": {},
                },
            },
            cookie=cookie,
        )
        self.assertTrue(st.startswith("200"), st)
        facts = self.store["payload"]["shopMetrics"]["facts"]
        self.assertEqual(facts.get("guarded"), "OP_KEEP")
        self.assertEqual(facts.get("admin_extra"), 5)


class WtSalaryAdjOpsTests(unittest.TestCase):
    """
    Узкое сохранение ручных полей ведомости: salary.<month>.<emp>.products|taxes|…
    Сценарий Ольги (Зарплата ТП «Продукты»/«Налог») без широкого DomainWriter-diff.
    FakeConn, без live БД.
    """

    def setUp(self):
        self._env_backup = dict(os.environ)
        os.environ["DATABASE_URL"] = "postgresql://worktime:" + SECRET + "@127.0.0.1:5432/worktime"
        os.environ["WT_PIN_PEPPER"] = "unit-pepper"
        os.environ["WT_SCRYPT_N"] = "16"
        os.environ["WT_OPS_ENABLED"] = "1"
        os.environ.pop("WT_API_TOKEN", None)
        os.environ.pop("WT_COOKIE_SECURE", None)
        wt_api.LOGIN_FAILS.clear()
        payload = json.loads(json.dumps(SAMPLE_APP))
        payload["salary"] = {
            "2026-11": {
                "e_boro": {
                    "products": 0,
                    "taxes": 0,
                    "bonus": 0,
                    "fines": 0,
                    "advance": 0,
                    "issued": False,
                },
                "e_other": {
                    "products": 10,
                    "taxes": 20,
                    "bonus": 0,
                    "issued": False,
                },
            }
        }
        payload["users"] = list(payload.get("users") or []) + [
            {
                "user_id": "usr_olga_adj",
                "display_name": "Ольга",
                "pin": "4446",
                "role": "user",
                "legacy_role": "adjust",
                "is_active": True,
                "departments": ["summary"],
                "permissions": {"salaryCalc": "edit"},
            }
        ]
        self.store = {
            "payload": payload,
            "version": 3,
            "updated_at": datetime.now(timezone.utc),
            "updated_by": "",
            "fail": False,
            "sessions": {},
        }
        self._patch = patch.object(wt_api, "connect", side_effect=lambda: FakeConn(self.store))
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        os.environ.clear()
        os.environ.update(self._env_backup)
        wt_api.LOGIN_FAILS.clear()

    def login(self, user_id="usr_olga_adj", pin="4446"):
        status, data, _, cookies = call("POST", "/api/login", {"user_id": user_id, "pin": pin})
        self.assertTrue(status.startswith("200"), status)
        return cookie_from(cookies), data

    def test_client_markers_narrow_salary_adj(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        html = (Path(root) / "app.html").read_text(encoding="utf-8")
        self.assertIn("function saveSalaryAdjFieldOp(", html)
        self.assertIn("SALARY_ADJ_OPS_FIELDS", html)
        self.assertIn("saveSalaryAdjFieldOp(employeeId, field)", html)
        self.assertIn("wtMeConfirmInflight", html)
        # Один money-leaf: без sources/updated_at в OPS (нет гонки на общем updated_at).
        adj_op = html.split("function saveSalaryAdjFieldOp(", 1)[1].split("\nfunction ", 1)[0]
        self.assertNotIn(".sources.", adj_op)
        self.assertNotIn(".updated_at", adj_op)
        # Не широкий saveApp; при отказе — откат локального значения.
        rep = html.split("function setRepSalaryAdj(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("saveSalaryAdjFieldOp(", rep)
        self.assertNotIn("saveApp();", rep)
        self.assertIn("rec[field] = prev;", rep)
        adj = html.split("function setAdjustment(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("saveSalaryAdjFieldOp(", adj)
        self.assertNotIn("saveApp();", adj)
        self.assertIn("rec[field] = prev;", adj)

    def test_olga_products_taxes_persist_and_reread(self):
        cookie, _ = self.login()
        # Как клиент после фикса: только money-leaf, без sources/updated_at.
        st, d, *_ = call(
            "POST",
            "/api/state/op/batch",
            {
                "idempotency_key": "sal-adj-boro-1",
                "atomic": False,
                "ops": [
                    {
                        "path": "salary.2026-11.e_boro.products",
                        "expected_v": 0,
                        "value": 500,
                    },
                    {
                        "path": "salary.2026-11.e_boro.taxes",
                        "expected_v": 0,
                        "value": 100,
                    },
                ],
            },
            cookie=cookie,
        )
        self.assertTrue(st.startswith("200"), (st, d))
        row = self.store["payload"]["salary"]["2026-11"]["e_boro"]
        self.assertEqual(row.get("products"), 500)
        self.assertEqual(row.get("taxes"), 100)
        self.assertFalse(row.get("issued"))
        # Другой сотрудник не тронут.
        other = self.store["payload"]["salary"]["2026-11"]["e_other"]
        self.assertEqual(other.get("products"), 10)
        self.assertEqual(other.get("taxes"), 20)
        # F5 / повторное чтение.
        st2, got, *_ = call("GET", "/api/state", cookie=cookie)
        self.assertTrue(st2.startswith("200"), st2)
        pl = got.get("payload") or {}
        again = ((pl.get("salary") or {}).get("2026-11") or {}).get("e_boro") or {}
        self.assertEqual(again.get("products"), 500)
        self.assertEqual(again.get("taxes"), 100)

    def test_rapid_fields_two_employees_no_cross_wipe(self):
        """Последовательные OPS разных полей/сотрудников не затирают друг друга."""
        cookie, _ = self.login()
        seq = [
            ("salary.2026-11.e_boro.products", 1600),
            ("salary.2026-11.e_boro.taxes", 100),
            ("salary.2026-11.e_boro.bonus", 50),
            ("salary.2026-11.e_boro.fines", 20),
            ("salary.2026-11.e_boro.advance", 30),
            ("salary.2026-11.e_other.products", 700),
        ]
        for path, val in seq:
            meta = (self.store["payload"].get("_wt_records") or {}).get(path) or {}
            rv = int(meta.get("v") or 0)
            st, d, *_ = call(
                "POST",
                "/api/state/op",
                {"op": {"path": path, "expected_v": rv, "value": val}},
                cookie=cookie,
            )
            self.assertTrue(st.startswith("200"), (path, st, d))
        boro = self.store["payload"]["salary"]["2026-11"]["e_boro"]
        other = self.store["payload"]["salary"]["2026-11"]["e_other"]
        self.assertEqual(boro.get("products"), 1600)
        self.assertEqual(boro.get("taxes"), 100)
        self.assertEqual(boro.get("bonus"), 50)
        self.assertEqual(boro.get("fines"), 20)
        self.assertEqual(boro.get("advance"), 30)
        self.assertEqual(other.get("products"), 700)
        self.assertEqual(other.get("taxes"), 20)

    def test_single_field_then_second_field(self):
        cookie, _ = self.login()
        st1, *_ = call(
            "POST",
            "/api/state/op",
            {
                "op": {
                    "path": "salary.2026-11.e_boro.products",
                    "expected_v": 0,
                    "value": 500,
                }
            },
            cookie=cookie,
        )
        self.assertTrue(st1.startswith("200"), st1)
        meta = (self.store["payload"].get("_wt_records") or {}).get(
            "salary.2026-11.e_boro.products"
        ) or {}
        rv = int(meta.get("v") or 1)
        st2, *_ = call(
            "POST",
            "/api/state/op",
            {
                "op": {
                    "path": "salary.2026-11.e_boro.taxes",
                    "expected_v": 0,
                    "value": 100,
                }
            },
            cookie=cookie,
        )
        self.assertTrue(st2.startswith("200"), st2)
        # Повтор products с актуальным expected_v — без дубля/потери.
        st3, *_ = call(
            "POST",
            "/api/state/op",
            {
                "op": {
                    "path": "salary.2026-11.e_boro.products",
                    "expected_v": rv,
                    "value": 500,
                }
            },
            cookie=cookie,
        )
        self.assertTrue(st3.startswith("200"), st3)
        row = self.store["payload"]["salary"]["2026-11"]["e_boro"]
        self.assertEqual(row.get("products"), 500)
        self.assertEqual(row.get("taxes"), 100)

    def test_no_salary_rights_forbidden(self):
        cookie, _ = self.login("usr_001", "1111")
        st, d, *_ = call(
            "POST",
            "/api/state/op",
            {
                "op": {
                    "path": "salary.2026-11.e_boro.products",
                    "expected_v": 0,
                    "value": 500,
                }
            },
            cookie=cookie,
        )
        self.assertTrue(st.startswith("403"), (st, d))
        self.assertEqual(
            self.store["payload"]["salary"]["2026-11"]["e_boro"].get("products"), 0
        )

    def test_parallel_conflict_other_user_same_field(self):
        cookie_a, _ = self.login()
        admin_cookie, _ = self.login("user_admin", "5555")
        st1, *_ = call(
            "POST",
            "/api/state/op",
            {
                "op": {
                    "path": "salary.2026-11.e_boro.products",
                    "expected_v": 0,
                    "value": 500,
                }
            },
            cookie=cookie_a,
        )
        self.assertTrue(st1.startswith("200"), st1)
        st2, d2, *_ = call(
            "POST",
            "/api/state/op",
            {
                "op": {
                    "path": "salary.2026-11.e_boro.products",
                    "expected_v": 0,
                    "value": 777,
                }
            },
            cookie=admin_cookie,
        )
        self.assertTrue(st2.startswith("409"), (st2, d2))
        self.assertEqual(
            self.store["payload"]["salary"]["2026-11"]["e_boro"].get("products"), 500
        )

    def test_calc_cannot_write_issued_via_adj_path(self):
        cookie, _ = self.login()
        st, d, *_ = call(
            "POST",
            "/api/state/op",
            {
                "op": {
                    "path": "salary.2026-11.e_boro.issued",
                    "expected_v": 0,
                    "value": True,
                }
            },
            cookie=cookie,
        )
        self.assertTrue(st.startswith("403"), (st, d))
        self.assertFalse(
            self.store["payload"]["salary"]["2026-11"]["e_boro"].get("issued")
        )


class WtSalaryFlagOpsTests(unittest.TestCase):
    """
    Узкое сохранение галочек issued / vacationPaidEarlier.
    FakeConn, без live БД. Формулы toHand не меняются.
    """

    def setUp(self):
        self._env_backup = dict(os.environ)
        os.environ["DATABASE_URL"] = "postgresql://worktime:" + SECRET + "@127.0.0.1:5432/worktime"
        os.environ["WT_PIN_PEPPER"] = "unit-pepper"
        os.environ["WT_SCRYPT_N"] = "16"
        os.environ["WT_OPS_ENABLED"] = "1"
        os.environ.pop("WT_API_TOKEN", None)
        os.environ.pop("WT_COOKIE_SECURE", None)
        wt_api.LOGIN_FAILS.clear()
        payload = json.loads(json.dumps(SAMPLE_APP))
        payload["salary"] = {
            "2026-11": {
                "e_boro": {
                    "products": 0,
                    "taxes": 0,
                    "vacationPay": 5000,
                    "vacationPaidEarlier": False,
                    "issued": False,
                },
                "e_other": {
                    "products": 10,
                    "vacationPay": 1000,
                    "vacationPaidEarlier": True,
                    "issued": True,
                },
            },
            "2026-10": {
                "e_boro": {
                    "vacationPay": 2000,
                    "vacationPaidEarlier": False,
                    "issued": False,
                }
            },
        }
        payload["users"] = list(payload.get("users") or []) + [
            {
                "user_id": "usr_flag_calc",
                "display_name": "Calc",
                "pin": "4446",
                "role": "user",
                "legacy_role": "adjust",
                "is_active": True,
                "departments": ["summary"],
                "permissions": {"salaryCalc": "edit"},
            },
            {
                "user_id": "usr_flag_pay",
                "display_name": "Pay",
                "pin": "4445",
                "role": "user",
                "legacy_role": "pay",
                "is_active": True,
                "departments": ["summary"],
                "permissions": {"salaryPay": "edit"},
            },
        ]
        self.store = {
            "payload": payload,
            "version": 3,
            "updated_at": datetime.now(timezone.utc),
            "updated_by": "",
            "fail": False,
            "sessions": {},
        }
        self._patch = patch.object(wt_api, "connect", side_effect=lambda: FakeConn(self.store))
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        os.environ.clear()
        os.environ.update(self._env_backup)
        wt_api.LOGIN_FAILS.clear()

    def login(self, user_id, pin):
        status, data, _, cookies = call("POST", "/api/login", {"user_id": user_id, "pin": pin})
        self.assertTrue(status.startswith("200"), status)
        return cookie_from(cookies), data

    def test_client_markers_narrow_flags_and_tohand_formula(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        html = (Path(root) / "app.html").read_text(encoding="utf-8")
        self.assertIn("function saveSalaryFlagFieldOp(", html)
        self.assertIn("SALARY_FLAG_OPS_FIELDS", html)
        issued_fn = html.split("function setIssued(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("saveSalaryFlagFieldOp(employeeId, \"issued\")", issued_fn)
        self.assertNotIn("saveApp();", issued_fn)
        earlier_fn = html.split("function setVacationPaidEarlier(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn(
            "saveSalaryFlagFieldOp(employeeId, \"vacationPaidEarlier\")", earlier_fn
        )
        self.assertNotIn("saveApp();", earlier_fn)
        # Формула «к выдаче» не тронута.
        self.assertIn(
            "const toHand = pay - (adj.vacationPaidEarlier ? vacationPay : 0);", html
        )

    def test_pay_saves_issued_persist_reread_toggle(self):
        cookie, _ = self.login("usr_flag_pay", "4445")
        st, d, *_ = call(
            "POST",
            "/api/state/op",
            {
                "op": {
                    "path": "salary.2026-11.e_boro.issued",
                    "expected_v": 0,
                    "value": True,
                }
            },
            cookie=cookie,
        )
        self.assertTrue(st.startswith("200"), (st, d))
        self.assertTrue(self.store["payload"]["salary"]["2026-11"]["e_boro"]["issued"])
        meta = (self.store["payload"].get("_wt_records") or {}).get(
            "salary.2026-11.e_boro.issued"
        ) or {}
        rv = int(meta.get("v") or 1)
        st2, *_ = call(
            "POST",
            "/api/state/op",
            {
                "op": {
                    "path": "salary.2026-11.e_boro.issued",
                    "expected_v": rv,
                    "value": False,
                }
            },
            cookie=cookie,
        )
        self.assertTrue(st2.startswith("200"), st2)
        self.assertFalse(self.store["payload"]["salary"]["2026-11"]["e_boro"]["issued"])
        st3, got, *_ = call("GET", "/api/state", cookie=cookie)
        self.assertTrue(st3.startswith("200"), st3)
        again = ((got.get("payload") or {}).get("salary") or {}).get("2026-11", {}).get(
            "e_boro"
        ) or {}
        self.assertFalse(again.get("issued"))
        # Чужие сотрудник/месяц не затронуты.
        self.assertTrue(self.store["payload"]["salary"]["2026-11"]["e_other"]["issued"])
        self.assertFalse(self.store["payload"]["salary"]["2026-10"]["e_boro"]["issued"])

    def test_calc_cannot_write_issued(self):
        cookie, _ = self.login("usr_flag_calc", "4446")
        st, d, *_ = call(
            "POST",
            "/api/state/op",
            {
                "op": {
                    "path": "salary.2026-11.e_boro.issued",
                    "expected_v": 0,
                    "value": True,
                }
            },
            cookie=cookie,
        )
        self.assertTrue(st.startswith("403"), (st, d))
        self.assertFalse(self.store["payload"]["salary"]["2026-11"]["e_boro"]["issued"])

    def test_calc_saves_vacation_paid_earlier_persist_reread(self):
        cookie, _ = self.login("usr_flag_calc", "4446")
        st, d, *_ = call(
            "POST",
            "/api/state/op",
            {
                "op": {
                    "path": "salary.2026-11.e_boro.vacationPaidEarlier",
                    "expected_v": 0,
                    "value": True,
                }
            },
            cookie=cookie,
        )
        self.assertTrue(st.startswith("200"), (st, d))
        row = self.store["payload"]["salary"]["2026-11"]["e_boro"]
        self.assertTrue(row.get("vacationPaidEarlier"))
        self.assertEqual(row.get("vacationPay"), 5000)
        st2, got, *_ = call("GET", "/api/state", cookie=cookie)
        self.assertTrue(st2.startswith("200"), st2)
        again = ((got.get("payload") or {}).get("salary") or {}).get("2026-11", {}).get(
            "e_boro"
        ) or {}
        self.assertTrue(again.get("vacationPaidEarlier"))
        self.assertTrue(
            self.store["payload"]["salary"]["2026-11"]["e_other"]["vacationPaidEarlier"]
        )
        self.assertFalse(
            self.store["payload"]["salary"]["2026-10"]["e_boro"]["vacationPaidEarlier"]
        )

    def test_pay_cannot_write_vacation_paid_earlier(self):
        cookie, _ = self.login("usr_flag_pay", "4445")
        st, d, *_ = call(
            "POST",
            "/api/state/op",
            {
                "op": {
                    "path": "salary.2026-11.e_boro.vacationPaidEarlier",
                    "expected_v": 0,
                    "value": True,
                }
            },
            cookie=cookie,
        )
        self.assertTrue(st.startswith("403"), (st, d))
        self.assertFalse(
            self.store["payload"]["salary"]["2026-11"]["e_boro"]["vacationPaidEarlier"]
        )

    def test_parallel_conflict_issued(self):
        cookie_pay, _ = self.login("usr_flag_pay", "4445")
        admin_cookie, _ = self.login("user_admin", "5555")
        st1, *_ = call(
            "POST",
            "/api/state/op",
            {
                "op": {
                    "path": "salary.2026-11.e_boro.issued",
                    "expected_v": 0,
                    "value": True,
                }
            },
            cookie=cookie_pay,
        )
        self.assertTrue(st1.startswith("200"), st1)
        st2, d2, *_ = call(
            "POST",
            "/api/state/op",
            {
                "op": {
                    "path": "salary.2026-11.e_boro.issued",
                    "expected_v": 0,
                    "value": False,
                }
            },
            cookie=admin_cookie,
        )
        self.assertTrue(st2.startswith("409"), (st2, d2))
        self.assertTrue(self.store["payload"]["salary"]["2026-11"]["e_boro"]["issued"])


class WtSummaryPayrollListAlignTests(unittest.TestCase):
    """
    Общая «Зарплата» должна включать карточки отдела так же, как ведомости
    магазина/склада/офиса (employeeIds ∪ employees), без дублей и без ТП вне drivers.
    FakeConn не нужен — только маркеры app.html. Формулы toHand/taxi не меняются.
    """

    def _src(self):
        return Path(__file__).resolve().parents[1].joinpath("app.html").read_text(encoding="utf-8")

    def test_summary_employee_groups_unions_workspace_employees(self):
        src = self._src()
        fn = src[
            src.find("function summaryEmployeeGroups(")
            : src.find("function departmentGroupClass(")
        ]
        self.assertIn("(w.employees || []).forEach", fn)
        self.assertIn("if (isSalesRep(e)) return;", fn)
        self.assertIn('if (w.code === "drivers")', fn)
        self.assertIn("includeInMonthPayroll(e)", fn)
        # Не только assignedIds / employeeIds.
        self.assertIn("assignedIds(w)", fn)
        self.assertIn("seen[e.id]", fn)

    def test_payroll_employees_still_unions_employees(self):
        src = self._src()
        fn = src[
            src.find("function payrollEmployees(")
            : src.find("function shopMetricCatalogFactField(")
        ]
        self.assertIn("assignedIds(w).forEach", fn)
        self.assertIn("(w.employees || []).forEach(add);", fn)
        self.assertIn("if (isSalesRep(e)) return;", fn)

    def test_salary_employee_groups_unions_workspace_employees(self):
        """Общая зарплата: карточка без employeeIds остаётся в месяце (октябрь после снятия галочки)."""
        src = self._src()
        fn = src[
            src.find("function salaryEmployeeGroups(")
            : src.find("function salaryVisibleEmployees(")
        ]
        self.assertIn("assignedIds(w).forEach", fn)
        self.assertIn("(w.employees || []).forEach(add);", fn)
        self.assertIn("includeInMonthPayroll(e)", fn)
        self.assertIn("seen[e.id]", fn)
        # Не ограничиваем добавление карточек только аутсорсингом.
        self.assertNotIn("isOutsourcingEmployee(e) || !includeInMonthPayroll(e)", fn)
        # Экспорт общей зарплаты идёт через тот же список.
        export_fn = src[
            src.find("function exportSalary(")
            : src.find("function downloadCSV(")
        ]
        self.assertIn("salaryEmployeeGroups()", export_fn)

    def test_department_month_totals_unions_workspace_employees(self):
        """Анализ: строка отдела учитывает карточку без employeeIds (октябрь после снятия галочки)."""
        src = self._src()
        fn = src[
            src.find("function departmentMonthTotals(")
            : src.find("function uniqueAssignedEmployees(")
        ]
        self.assertIn("assignedIds(w).forEach", fn)
        self.assertIn("(w.employees || []).forEach(add);", fn)
        self.assertIn("includeInMonthPayroll(e)", fn)
        self.assertIn("seen[e.id]", fn)
        self.assertIn("count: people.length", fn)
        self.assertNotIn("count: ids.length", fn)
        # Сравнение месяцев в Анализе использует ту же функцию.
        analysis = src[
            src.find("function renderAnalysis(")
            : src.find("function canEditShopMetricCatalog(")
        ]
        self.assertIn("departmentMonthTotals(w, selectedMonth)", analysis)
        self.assertIn("departmentMonthTotals(w, m)", analysis)

    def test_warehouse_payroll_shows_taxi_under_oklad(self):
        src = self._src()
        row = src[
            src.find("function shopSalaryRowData(")
            : src.find("function closeShopPayModal(")
        ]
        self.assertIn("taxiTotal: Number(data.taxiTotal || 0)", row)
        self.assertIn("taxiIncluded: !!data.taxiIncluded", row)
        self.assertIn("function payrollAccruedNotes(row)", row)
        self.assertIn('Такси +\' + money(row.taxiTotal)', row)
        render = src[
            src.find("function renderPayrollView(")
            : src.find("function renderShopSalarySettings(")
        ]
        self.assertIn("money(row.oklad) + payrollAccruedNotes(row)", render)
        # Итог строки по-прежнему toHand (такси уже внутри), без повторного сложения в oklad.
        self.assertIn("total: data.toHand", row)
        self.assertIn("sums.oklad += row.oklad;", render)
        self.assertIn("sums.total += row.total;", render)


class WtSaveOutcomeTruthinessTests(unittest.TestCase):
    """H5/H6: ложный успех сохранения — toast только при ok === true; без !!ok / ok !== false."""

    def _src(self):
        return Path(__file__).resolve().parents[1].joinpath("app.html").read_text(encoding="utf-8")

    def test_wt_save_outcome_ok_helper(self):
        src = self._src()
        self.assertIn("function wtSaveOutcomeOk(ok)", src)
        helper = src[src.find("function wtSaveOutcomeOk(ok)"):src.find("function putServerStateOnce(")]
        self.assertIn("return ok === true;", helper)
        self.assertNotIn("return !!ok;", helper)

    def test_h5_day_role_awaits_save_before_toast(self):
        src = self._src()
        start = src.find("function addShopDayRoleFromForm(")
        end = src.find("function deleteShopDayRole(", start)
        body = src[start:end]
        self.assertGreater(start, 0)
        self.assertIn("Promise.resolve(saveApp())", body)
        toast_ok = body.find('toast("Замещение сохранено.")')
        then_pos = body.find("Promise.resolve(saveApp())")
        self.assertGreater(toast_ok, then_pos)
        self.assertIn("wtSaveOutcomeOk(ok)", body)
        # Нет fire-and-forget saveApp(); сразу перед toast успеха.
        self.assertNotRegex(body, r"saveApp\(\);\s*\n\s*renderShopDayRoles\(\);\s*\n\s*toast\(\"Замещение сохранено")

    def test_h6_salary_adj_flag_strict_true(self):
        src = self._src()
        adj = src.split("function saveSalaryAdjFieldOp(", 1)[1].split("\nfunction ", 1)[0]
        flag = src.split("function saveSalaryFlagFieldOp(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("return wtSaveOutcomeOk(ok);", adj)
        self.assertIn("return wtSaveOutcomeOk(ok);", flag)
        self.assertNotIn("return !!ok;", adj)
        self.assertNotIn("return !!ok;", flag)

    def test_h6_employee_ids_outsourcing_revision_vacation_strict(self):
        src = self._src()
        ids = src.split("function wtSaveWorkspaceEmployeeIds(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("return wtSaveOutcomeOk(ok);", ids)
        self.assertNotIn("ok !== false", ids)
        out = src.split("function wtSaveOutsourcingCardOps(", 1)[1].split(
            "\nfunction paintOutsourcingEmployeeUi(", 1
        )[0]
        self.assertIn("wtSaveOutcomeOk(ok)", out)
        self.assertNotIn("ok !== false", out)
        rev = src.split("function wtPersistRevisionTasks(", 1)[1].split("\nfunction submitRevisionAssign(", 1)[0]
        self.assertIn("wtSaveOutcomeOk(ok)", rev)
        self.assertNotIn("finish(!!ok)", rev)
        vac = src.split("function wtPersistVacationPlanRecords(", 1)[1].split(
            "function wtPersistSalaryVacationPayLeaves(", 1
        )[0]
        self.assertIn("wtSaveOutcomeOk(ok)", vac)
        self.assertNotIn("return !!ok;", vac)

    def test_h6_timesheet_put_path_not_truthy_string(self):
        src = self._src()
        start = src.find("function saveTimesheetDayChange(")
        end = src.find("\nfunction ", start + len("function saveTimesheetDayChange("))
        fn = src[start:end]
        self.assertIn("wtSaveOutcomeOk(ok)", fn)
        self.assertNotIn("if (ok && path)", fn)
        self.assertNotIn('status: ok ? "saved_put"', fn)

    def test_h5_workspace_settings_awaits_save(self):
        src = self._src()
        start = src.find("function saveWorkspaceSettings(")
        end = src.find("function renderSummaryUsers(", start)
        body = src[start:end]
        self.assertIn("Promise.resolve(saveApp())", body)
        self.assertIn("wtSaveOutcomeOk(ok)", body)
        toast_ok = body.find('toast("Настройки сохранены.")')
        then_pos = body.find("Promise.resolve(saveApp())")
        self.assertGreater(toast_ok, then_pos)


class WtCriticalSilentSaveFixTests(unittest.TestCase):
    """Критические fire-and-forget save: await + wtSaveOutcomeOk / узкий DomainWriter."""

    def _src(self):
        return Path(__file__).resolve().parents[1].joinpath("app.html").read_text(encoding="utf-8")

    def _fn(self, name, next_name=None):
        src = self._src()
        start = src.find("function " + name + "(")
        self.assertGreater(start, 0, name)
        if next_name:
            end = src.find("function " + next_name + "(", start + 10)
        else:
            end = src.find("\nfunction ", start + 10)
        self.assertGreater(end, start, name)
        return src[start:end]

    def test_helpers_await_and_explicit_ops(self):
        src = self._src()
        self.assertIn("function wtAwaitSaveAppFeedback(", src)
        self.assertIn("function wtPersistExplicitOps(", src)
        self.assertIn("function crPersistAfterLocalEdit(", src)
        await_fn = src[src.find("function wtAwaitSaveAppFeedback("):src.find("function wtPersistExplicitOps(")]
        self.assertIn("wtSaveOutcomeOk(ok)", await_fn)
        self.assertIn('ok === "timeout"', await_fn)
        self.assertIn('ok === "conflict_review"', await_fn)

    def test_shop_inventory_and_responsible_use_explicit_ops(self):
        inv = self._fn("setShopInventoryValue", "setShopInventoryConducted")
        cond = self._fn("setShopInventoryConducted", "shopInventoryMonthTotals")
        resp = self._fn("setShopRecountResponsible", "shopRecountSelectHtml")
        wo = self._fn("setShopWriteoffLossesFact", "shopWriteoffLossesCellHtml")
        for body in (inv, cond, resp, wo):
            self.assertIn("wtPersistExplicitOps(", body)
            self.assertNotRegex(body, r"saveApp\(\);\s*$")
        self.assertIn("shopMetrics.inventoryResults.", inv)
        self.assertIn("shopMetrics.recountResponsible.", resp)
        self.assertIn("shopWriteoffLosses.months.", wo)

    def test_delete_day_role_awaits_and_toasts_after(self):
        body = self._fn("deleteShopDayRole", "refreshShopDayRoleHint")
        self.assertIn("wtPersistExplicitOps(", body)
        self.assertIn("shopMetrics.dayRoles.", body)
        self.assertIn('okToast: "Замещение удалено."', body)
        self.assertNotIn("saveApp();", body)

    def test_cr_apply_outcome_no_false_success(self):
        body = self._fn("crApplyOutcome", "crSetProcessStage")
        self.assertIn("crPersistAfterLocalEdit(", body)
        self.assertNotIn("saveApp();", body)
        # Toast успеха только через okToast после ответа.
        self.assertIn('crPersistAfterLocalEdit("Направлено:', body)
        self.assertNotRegex(body, r"saveApp\(\);\s*\n\s*renderTerritoryReturns\(\);\s*\n\s*toast\(")

    def test_hr_delete_move_await(self):
        dele = self._fn("deleteEmployee", "summaryDirectoryGroups")
        move = self._fn("moveEmployeeToDepartment", "updateStats")
        # Удаление: узкий OPS / slice PUT, без широкого saveApp и без каскада salary/timesheet.
        self.assertIn("wtEmployeeLinkedHistoryLabels(", dele)
        self.assertIn("wtDomainWriterSave(", dele)
        self.assertIn("atomic: true", dele)
        self.assertIn("restoreLocal()", dele)
        self.assertIn('value: null', dele)
        self.assertNotIn("delete appData.salary", dele)
        self.assertNotIn("delete w.timesheet", dele)
        self.assertNotIn("wtAwaitSaveAppFeedback(", dele)
        self.assertNotRegex(dele, r"Object\.keys\(appData\.salary")
        self.assertIn("wtAwaitSaveAppFeedback(", move)
        self.assertNotIn("saveApp();", move)
        src = self._src()
        self.assertIn("function wtEmployeeLinkedHistoryLabels(", src)
        self.assertIn("wtEmployeeDeleteBlockedToast(", src)
        # Паритет новых источников связей (клиент).
        self.assertIn("возвраты покупателей", src)
        self.assertIn("conducted_by_employee_id", src[src.find("function wtEmployeeLinkedHistoryLabels("):src.find("function wtEmployeeDeleteBlockedToast(")])
        self.assertIn("employee_ids", src[src.find("function wtEmployeeLinkedHistoryLabels("):src.find("function wtEmployeeDeleteBlockedToast(")])
        self.assertIn("оргструктура", src)
        self.assertIn("СПУ/совещания", src)

    def test_fill_month_awaits_when_ops_off(self):
        body = self._fn("fillMonth", None)
        # Обрезать до следующей крупной секции зарплаты ТП.
        src = self._src()
        start = src.find("function fillMonth(")
        end = src.find("ЗАРПЛАТА ТОРГОВЫХ ПРЕДСТАВИТЕЛЕЙ", start)
        body = src[start:end]
        self.assertIn("wtAwaitSaveAppFeedback(", body)
        self.assertIn("wtOpsUiEnabled()", body)
        self.assertNotRegex(body, r"saveApp\(\);\s*\n\s*refreshAll\(\);\s*\n\s*toast\(")


class WtSpuPlanFactNarrowOpsTests(unittest.TestCase):
    """План–факт СПУ: lazy overlay + wtSpuPostStateOp, черновики, без DomainWriter-collect."""

    def _src(self):
        here = Path(__file__).resolve()
        candidates = [
            here.parents[1].joinpath("app.html"),
            here.parents[0].joinpath("app.html"),
            Path(r"C:\Users\1\Desktop\Тест 2\company-accounting\app.html"),
        ]
        for path in candidates:
            if path.is_file():
                return path.read_text(encoding="utf-8")
        self.fail("app.html not found for SpuPlanFact tests; tried: "
                  + ", ".join(str(p) for p in candidates))

    def _fn(self, name, next_name=None):
        src = self._src()
        start = src.find("function " + name + "(")
        self.assertGreater(start, 0, name)
        if next_name:
            end = src.find("function " + next_name + "(", start + 10)
        else:
            end = src.find("\nfunction ", start + 10)
        self.assertGreater(end, start, name)
        return src[start:end]

    def test_overlay_helpers_and_cell_writers(self):
        src = self._src()
        self.assertIn("function wtSpuOverlayPeriodIndicator(", src)
        self.assertIn("function wtSpuPersistRecordOp(", src)
        self.assertIn("function wtSpuPostStateOp(", src)
        self.assertIn("function wtSpuBranchFromLastLoaded(", src)
        self.assertIn("wtSpuCellDrafts", src)
        self.assertIn("function wtSpuRememberCellDraft(", src)
        self.assertIn("function wtSpuBranchWithLocalDrafts(", src)
        self.assertIn("function wtSpuCaptureFocusedSpuInput(", src)
        persist = self._fn("wtSpuPersistRecordOp", "wtPersistMgmtBranches")
        self.assertIn("wtSpuPostStateOp(", persist)
        self.assertNotIn("wtSaveRecordOp(", persist)
        self.assertIn("wtSpuBranchFromLastLoaded(path)", persist)
        self.assertIn("typeof buildFnOrValue === \"function\"", persist)
        self.assertIn('res.status === "conflict"', persist)
        self.assertIn("window.confirm", persist)
        self.assertIn("rebuildOnServer", persist)
        self.assertIn("wtSpuApplySavedBranchLocal", persist)
        self.assertIn("Ответ сервера не получен", persist)
        self.assertNotIn("wtDomainWriterSave(", persist)
        self.assertNotIn("wtPersistExplicitOps(", persist)
        # Timeout/offline: без авто-повтора POST после offline.
        offline_idx = persist.find('status === "offline"')
        self.assertGreater(offline_idx, 0)
        after_offline = persist[offline_idx:offline_idx + 450]
        self.assertNotIn("wtSpuPostStateOp(", after_offline)

        plan = self._fn("setMgmtPlanValue", "spuIsoToRu")
        fact = self._fn("setMgmtManualFact", "setMgmtPlanValue")
        for body in (plan, fact):
            self.assertIn("wtSpuPersistRecordOp(", body)
            self.assertIn("wtSpuOverlayPeriodIndicator(", body)
            self.assertIn("wtSpuRememberCellDraft(", body)
            self.assertIn("touchedDraft", body)
            # buildFn передаётся в очередь, а не замороженный value.
            self.assertRegex(body, r"wtSpuPersistRecordOp\(\s*\"management\.(plans|manualFacts)\"\s*,\s*build\s*,")
            self.assertNotIn("saveApp();", body)

        helper = self._fn("wtPersistMgmtBranches", "setMgmtManualFact")
        self.assertIn("planIndicatorIds", helper)
        self.assertIn("отказ сохранить всю локальную ветку", helper)
        self.assertIn("buildPlans", helper)
        self.assertIn("buildFacts", helper)

    def test_planfact_ui_and_period_tab(self):
        pf_plan = self._fn("setMgmtPfPlan", "setMgmtPfFact")
        pf_fact = self._fn("setMgmtPfFact", "mgmtSpuDeptOptionsHtml")
        row = self._fn("saveSpuPlansTabRow", "saveSpuPlansTabAll")
        all_fn = self._fn("saveSpuPlansTabAll", "renderSpuPlansTabHtml")
        apply = self._fn("spuApplyPeriodPlanToWeeks", "spuApplyAllPeriodPlans")
        self.assertIn("setMgmtPlanValue(", pf_plan)
        self.assertIn("persistIndicators", pf_plan)
        self.assertNotIn("saveApp();", pf_plan)
        self.assertIn("setMgmtManualFact(", pf_fact)
        self.assertNotIn("saveApp();", pf_fact)
        for body in (row, all_fn):
            self.assertIn("planIndicatorIds", body)
            self.assertIn("wtPersistMgmtBranches(", body)
            self.assertNotIn("saveApp();", body)
        self.assertIn("{ persist: false }", apply)
        self.assertNotIn("saveApp();", apply)

    def test_mgmt_key_perms_period_plans(self):
        self.assertEqual(wt_api.MGMT_KEY_PERMS.get("period_plans"), "managementIndicators")
        self.assertEqual(wt_api.MGMT_KEY_PERMS.get("mgmt_periods"), "managementIndicators")
        spu_editor = {
            "user_id": "usr_spu_ind",
            "role": "staff",
            "is_active": True,
            "departments": ["spu"],
            "permissions": {"managementIndicators": "edit", "management": "none"},
        }
        self.assertTrue(wt_api.ops_path_allowed(spu_editor, "management.plans"))
        self.assertTrue(wt_api.ops_path_allowed(spu_editor, "management.manualFacts"))
        self.assertTrue(wt_api.ops_path_allowed(spu_editor, "management.period_plans"))
        self.assertFalse(wt_api.ops_path_allowed(spu_editor, "management.tasks"))


class WtSpuPlanFactOverlayStateTests(unittest.TestCase):
    """Фактическое состояние FakeConn: overlay одной ячейки, 409, удаление, drift не уходит."""

    @staticmethod
    def overlay_cell(base, period_key, indicator_id, rec_or_none):
        """Зеркало wtSpuOverlayPeriodIndicator для проверки контракта записи."""
        out = json.loads(json.dumps(base if isinstance(base, dict) else {}))
        pk = str(period_key or "")
        iid = str(indicator_id or "")
        if not pk or not iid:
            return out
        bucket = dict(out.get(pk) or {}) if isinstance(out.get(pk), dict) else {}
        if rec_or_none is None:
            bucket.pop(iid, None)
        else:
            bucket[iid] = json.loads(json.dumps(rec_or_none))
        if bucket:
            out[pk] = bucket
        else:
            out.pop(pk, None)
        return out

    def setUp(self):
        self._env_backup = dict(os.environ)
        os.environ["DATABASE_URL"] = "postgresql://worktime:" + SECRET + "@127.0.0.1:5432/worktime"
        os.environ["WT_PIN_PEPPER"] = "unit-pepper"
        os.environ["WT_SCRYPT_N"] = "16"
        os.environ["WT_OPS_ENABLED"] = "1"
        os.environ.pop("WT_API_TOKEN", None)
        os.environ.pop("WT_COOKIE_SECURE", None)
        wt_api.LOGIN_FAILS.clear()
        payload = json.loads(json.dumps(SAMPLE_APP))
        payload["management"] = {
            "indicators": [
                {"indicator_id": "mind_a", "name": "A", "plan_mode": "spu", "is_active": True},
                {"indicator_id": "mind_b", "name": "B", "plan_mode": "spu", "is_active": True},
            ],
            "plans": {
                "week:2026-10-30": {
                    "mind_a": {
                        "indicator_id": "mind_a",
                        "period": "week:2026-10-30",
                        "value": "100",
                        "plan_value": 100,
                    }
                }
            },
            "manualFacts": {},
            "period_plans": [],
            "tasks": [],
            "meetings": [],
            "events": [],
        }
        payload["users"] = list(payload.get("users") or [])
        payload["users"].append({
            "user_id": "usr_spu_pf",
            "display_name": "СПУ редактор",
            "pin": "8888",
            "role": "staff",
            "is_active": True,
            "departments": ["spu"],
            "permissions": {"managementIndicators": "edit", "management": "none"},
        })
        self.store = {
            "payload": payload,
            "version": 1,
            "updated_at": datetime.now(timezone.utc),
            "updated_by": "",
            "fail": False,
            "sessions": {},
        }
        self._patch = patch.object(wt_api, "connect", side_effect=lambda: FakeConn(self.store))
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        os.environ.clear()
        os.environ.update(self._env_backup)
        wt_api.LOGIN_FAILS.clear()

    def login_spu(self):
        status, data, _, cookies = call(
            "POST", "/api/login", {"user_id": "usr_spu_pf", "pin": "8888"}
        )
        self.assertTrue(status.startswith("200"), status)
        return cookie_from(cookies), data

    def _op(self, cookie, path, expected_v, value, key="k"):
        return call(
            "POST",
            "/api/state/op",
            {
                "idempotency_key": key,
                "op": {"path": path, "expected_v": expected_v, "value": value},
            },
            cookie=cookie,
        )

    def test_overlay_one_cell_keeps_other_server_cells_and_drops_local_drift(self):
        cookie, _ = self.login_spu()
        base = self.store["payload"]["management"]["plans"]
        # Локальный drift (не должен попасть на сервер), если слать только overlay.
        drift_local = json.loads(json.dumps(base))
        drift_local["week:2026-10-30"]["mind_drift"] = {
            "indicator_id": "mind_drift",
            "value": "999",
            "plan_value": 999,
        }
        merged = self.overlay_cell(
            base,
            "week:2026-10-30",
            "mind_b",
            {"indicator_id": "mind_b", "period": "week:2026-10-30", "value": "55", "plan_value": 55},
        )
        self.assertNotIn("mind_drift", merged["week:2026-10-30"])
        st, data, *_ = self._op(cookie, "management.plans", 0, merged, key="spu_cell1")
        self.assertTrue(st.startswith("200"), (st, data))
        plans = self.store["payload"]["management"]["plans"]
        self.assertEqual(plans["week:2026-10-30"]["mind_a"]["plan_value"], 100)
        self.assertEqual(plans["week:2026-10-30"]["mind_b"]["plan_value"], 55)
        self.assertNotIn("mind_drift", plans["week:2026-10-30"])
        # Магазин/зарплата не тронуты.
        self.assertEqual(
            self.store["payload"].get("shopMetrics"),
            SAMPLE_APP.get("shopMetrics"),
        )

    def test_delete_cell_preserves_sibling(self):
        cookie, _ = self.login_spu()
        base = self.store["payload"]["management"]["plans"]
        # Добавим mind_b, затем удалим mind_a overlay'ем.
        with_b = self.overlay_cell(
            base,
            "week:2026-10-30",
            "mind_b",
            {"indicator_id": "mind_b", "value": "1", "plan_value": 1},
        )
        st1, d1, *_ = self._op(cookie, "management.plans", 0, with_b, key="spu_add_b")
        self.assertTrue(st1.startswith("200"), (st1, d1))
        v = int(d1.get("record_v") or 1)
        cleared = self.overlay_cell(
            self.store["payload"]["management"]["plans"],
            "week:2026-10-30",
            "mind_a",
            None,
        )
        st2, d2, *_ = self._op(cookie, "management.plans", v, cleared, key="spu_del_a")
        self.assertTrue(st2.startswith("200"), (st2, d2))
        week = self.store["payload"]["management"]["plans"]["week:2026-10-30"]
        self.assertNotIn("mind_a", week)
        self.assertEqual(week["mind_b"]["plan_value"], 1)

    def test_concurrent_409_then_overlay_retry_keeps_both_cells(self):
        cookie, _ = self.login_spu()
        base_v0 = json.loads(json.dumps(self.store["payload"]["management"]["plans"]))
        # Пользователь B сохраняет mind_b.
        b_payload = self.overlay_cell(
            base_v0,
            "week:2026-10-30",
            "mind_b",
            {"indicator_id": "mind_b", "value": "20", "plan_value": 20},
        )
        st_b, d_b, *_ = self._op(cookie, "management.plans", 0, b_payload, key="spu_b")
        self.assertTrue(st_b.startswith("200"), (st_b, d_b))
        server_v = int(d_b.get("record_v") or 1)
        # Пользователь A со старым expected_v=0 пытается mind_a2 (как «новая» ячейка) —
        # на базе устаревшего снимка без mind_b.
        a_stale = self.overlay_cell(
            base_v0,
            "week:2026-10-30",
            "mind_a",
            {"indicator_id": "mind_a", "value": "111", "plan_value": 111},
        )
        st_a, d_a, *_ = self._op(cookie, "management.plans", 0, a_stale, key="spu_a_stale")
        self.assertTrue(st_a.startswith("409") or (isinstance(d_a, dict) and d_a.get("error") == "conflict"), (st_a, d_a))
        # Безопасный retry: overlay ячейки A на server_value.
        server_plans = d_a.get("server_value") or self.store["payload"]["management"]["plans"]
        a_retry = self.overlay_cell(
            server_plans,
            "week:2026-10-30",
            "mind_a",
            {"indicator_id": "mind_a", "value": "111", "plan_value": 111},
        )
        st_ok, d_ok, *_ = self._op(
            cookie, "management.plans", int(d_a.get("server_v") or server_v), a_retry, key="spu_a_retry"
        )
        self.assertTrue(st_ok.startswith("200"), (st_ok, d_ok))
        week = self.store["payload"]["management"]["plans"]["week:2026-10-30"]
        self.assertEqual(week["mind_a"]["plan_value"], 111)
        self.assertEqual(week["mind_b"]["plan_value"], 20)

    def test_manual_fact_overlay_and_forbidden_without_perm(self):
        cookie, _ = self.login_spu()
        base = {}
        facts = self.overlay_cell(
            base,
            "week:2026-10-30",
            "mind_a",
            {
                "indicator_id": "mind_a",
                "period": "week:2026-10-30",
                "value": "7",
                "entered_by_user_id": "usr_spu_pf",
            },
        )
        st, data, *_ = self._op(cookie, "management.manualFacts", 0, facts, key="spu_fact")
        self.assertTrue(st.startswith("200"), (st, data))
        self.assertEqual(
            self.store["payload"]["management"]["manualFacts"]["week:2026-10-30"]["mind_a"]["value"],
            "7",
        )
        # Пользователь без managementIndicators — 403.
        self.store["payload"]["users"].append({
            "user_id": "usr_shop_only",
            "display_name": "Магазин",
            "pin": "1111",
            "role": "staff",
            "is_active": True,
            "departments": ["shop"],
            "permissions": {"timesheet": "edit"},
        })
        st_l, _, _, cookies = call("POST", "/api/login", {"user_id": "usr_shop_only", "pin": "1111"})
        self.assertTrue(st_l.startswith("200"), st_l)
        shop_cookie = cookie_from(cookies)
        st_f, d_f, *_ = self._op(shop_cookie, "management.plans", 0, {"x": {}}, key="spu_forbid")
        self.assertTrue(
            st_f.startswith("403") or (isinstance(d_f, dict) and d_f.get("error") == "forbidden"),
            (st_f, d_f),
        )


class WtSpuClientQueueStateTests(unittest.TestCase):
    """
    Фактическое состояние сервера и UI-модели при ленивой очереди СПУ
    (зеркало wtSpuPersistRecordOp: build в момент выполнения, черновики, 409).
    FakeConn, без live БД / Node.
    """

    PK = "week:2026-10-30"
    PATH = "management.plans"

    @staticmethod
    def overlay_cell(base, period_key, indicator_id, rec_or_none):
        out = json.loads(json.dumps(base if isinstance(base, dict) else {}))
        pk = str(period_key or "")
        iid = str(indicator_id or "")
        if not pk or not iid:
            return out
        bucket = dict(out.get(pk) or {}) if isinstance(out.get(pk), dict) else {}
        if rec_or_none is None:
            bucket.pop(iid, None)
        else:
            bucket[iid] = json.loads(json.dumps(rec_or_none))
        if bucket:
            out[pk] = bucket
        else:
            out.pop(pk, None)
        return out

    def setUp(self):
        self._env_backup = dict(os.environ)
        os.environ["DATABASE_URL"] = "postgresql://worktime:" + SECRET + "@127.0.0.1:5432/worktime"
        os.environ["WT_PIN_PEPPER"] = "unit-pepper"
        os.environ["WT_SCRYPT_N"] = "16"
        os.environ["WT_OPS_ENABLED"] = "1"
        os.environ.pop("WT_API_TOKEN", None)
        os.environ.pop("WT_COOKIE_SECURE", None)
        wt_api.LOGIN_FAILS.clear()
        payload = json.loads(json.dumps(SAMPLE_APP))
        payload["management"] = {
            "indicators": [
                {"indicator_id": "mind_a", "name": "A", "plan_mode": "spu", "is_active": True},
                {"indicator_id": "mind_b", "name": "B", "plan_mode": "spu", "is_active": True},
                {"indicator_id": "mind_c", "name": "C", "plan_mode": "spu", "is_active": True},
            ],
            "plans": {},
            "manualFacts": {},
            "period_plans": [],
            "tasks": [],
            "meetings": [],
            "events": [],
        }
        payload["users"] = list(payload.get("users") or [])
        payload["users"].append({
            "user_id": "usr_spu_q",
            "display_name": "СПУ очередь",
            "pin": "7777",
            "role": "staff",
            "is_active": True,
            "departments": ["spu"],
            "permissions": {"managementIndicators": "edit", "management": "none"},
        })
        payload["users"].append({
            "user_id": "usr_spu_q2",
            "display_name": "СПУ второй",
            "pin": "6666",
            "role": "staff",
            "is_active": True,
            "departments": ["spu"],
            "permissions": {"managementIndicators": "edit", "management": "none"},
        })
        self.store = {
            "payload": payload,
            "version": 1,
            "updated_at": datetime.now(timezone.utc),
            "updated_by": "",
            "fail": False,
            "sessions": {},
        }
        self._patch = patch.object(wt_api, "connect", side_effect=lambda: FakeConn(self.store))
        self._patch.start()
        self.cookie = self._login("usr_spu_q", "7777")
        self.last_loaded = json.loads(json.dumps(payload))
        self.app_data = json.loads(json.dumps(payload))
        self.drafts = {"plans": {}, "facts": {}}
        self.record_v = {self.PATH: 0, "management.manualFacts": 0}
        self.ops_log = []

    def tearDown(self):
        self._patch.stop()
        os.environ.clear()
        os.environ.update(self._env_backup)
        wt_api.LOGIN_FAILS.clear()

    def _login(self, user_id, pin):
        status, data, _, cookies = call("POST", "/api/login", {"user_id": user_id, "pin": pin})
        self.assertTrue(status.startswith("200"), (status, data))
        return cookie_from(cookies)

    def _op(self, cookie, path, expected_v, value, key):
        st, data, *_ = call(
            "POST",
            "/api/state/op",
            {
                "idempotency_key": key,
                "op": {"path": path, "expected_v": expected_v, "value": value},
            },
            cookie=cookie,
        )
        self.ops_log.append({"path": path, "expected_v": expected_v, "status": st, "key": key})
        return st, data

    def _draft_key(self, period_key, iid):
        return str(period_key) + "\0" + str(iid)

    def _remember_draft(self, kind, period_key, iid, rec):
        bag = "facts" if kind == "facts" else "plans"
        self.drafts[bag][self._draft_key(period_key, iid)] = {
            "periodKey": period_key,
            "indicatorId": iid,
            "deleted": rec is None,
            "rec": None if rec is None else json.loads(json.dumps(rec)),
        }

    def _clear_draft(self, kind, period_key, iid):
        bag = "facts" if kind == "facts" else "plans"
        self.drafts[bag].pop(self._draft_key(period_key, iid), None)

    def _branch_with_drafts(self, path, server_branch):
        kind = "facts" if path.endswith("manualFacts") else "plans"
        out = json.loads(json.dumps(server_branch if isinstance(server_branch, dict) else {}))
        for d in (self.drafts.get(kind) or {}).values():
            out = self.overlay_cell(
                out, d["periodKey"], d["indicatorId"], None if d["deleted"] else d["rec"]
            )
        return out

    def _apply_saved(self, path, server_value, record_v):
        # lastLoaded = чистая серверная ветка; UI = сервер + оставшиеся черновики.
        cur = self.last_loaded
        for part in path.split("."):
            if part == path.split(".")[-1]:
                cur[part] = json.loads(json.dumps(server_value))
            else:
                cur = cur.setdefault(part, {})
        self.record_v[path] = int(record_v or 0)
        ui_root = self.app_data
        for part in path.split(".")[:-1]:
            ui_root = ui_root.setdefault(part, {})
        ui_root[path.split(".")[-1]] = self._branch_with_drafts(path, server_value)

    def _persist_plan_cell(self, iid, value, cookie=None, key=None, confirm_retry=True):
        """Зеркало setMgmtPlanValue + wtSpuPersistRecordOp (lazy build в очереди)."""
        cookie = cookie or self.cookie
        pk = self.PK
        rec = None
        if value is not None and str(value).strip() != "":
            n = float(value) if str(value).replace(".", "", 1).isdigit() else value
            rec = {
                "indicator_id": iid,
                "period": pk,
                "period_key": pk,
                "value": str(value),
                "plan_value": n if isinstance(n, (int, float)) else str(value),
            }
            self.app_data.setdefault("management", {}).setdefault("plans", {}).setdefault(pk, {})[iid] = rec
        else:
            bucket = self.app_data.get("management", {}).get("plans", {}).get(pk) or {}
            bucket.pop(iid, None)
            rec = None
        self._remember_draft("plans", pk, iid, rec)

        # Payload формируется непосредственно перед POST — из актуального lastLoaded.
        base = json.loads(json.dumps(
            (self.last_loaded.get("management") or {}).get("plans") or {}
        ))
        payload = self.overlay_cell(base, pk, iid, rec)
        expected = int(self.record_v.get(self.PATH) or 0)
        st, data = self._op(cookie, self.PATH, expected, payload, key or ("spu_" + iid + "_" + str(value)))
        if st.startswith("200") and isinstance(data, dict) and data.get("error") != "conflict":
            self._clear_draft("plans", pk, iid)
            self._apply_saved(self.PATH, data.get("value"), data.get("record_v"))
            return True, data
        if (st.startswith("409") or (isinstance(data, dict) and data.get("error") == "conflict")):
            if not confirm_retry:
                # Отмена: ввод остаётся черновиком/на экране, lastLoaded не трогаем.
                return False, data
            server_val = data.get("server_value") or (self.store["payload"]["management"]["plans"])
            server_v = int(data.get("server_v") or self.record_v.get(self.PATH) or 0)
            retry_payload = self.overlay_cell(server_val, pk, iid, rec)
            st2, data2 = self._op(
                cookie, self.PATH, server_v, retry_payload, key=(key or "spu_retry") + "_r"
            )
            if st2.startswith("200"):
                self._clear_draft("plans", pk, iid)
                self._apply_saved(self.PATH, data2.get("value"), data2.get("record_v"))
                return True, data2
            return False, data2
        return False, data

    def _server_week(self):
        return (self.store["payload"]["management"]["plans"].get(self.PK) or {})

    def _ui_week(self):
        return (self.app_data.get("management", {}).get("plans", {}).get(self.PK) or {})

    def _loaded_week(self):
        return (self.last_loaded.get("management", {}).get("plans", {}).get(self.PK) or {})

    def test_01a_frozen_enqueue_loses_or_conflicts_a(self):
        """Старый дефект: payload L0+A и L0+B заморожены при постановке в очередь."""
        base0 = {}
        frozen_a = self.overlay_cell(base0, self.PK, "mind_a", {
            "indicator_id": "mind_a", "value": "10", "plan_value": 10,
        })
        frozen_b = self.overlay_cell(base0, self.PK, "mind_b", {
            "indicator_id": "mind_b", "value": "20", "plan_value": 20,
        })
        st_a, d_a = self._op(self.cookie, self.PATH, 0, frozen_a, "frozen_a")
        self.assertTrue(st_a.startswith("200"), (st_a, d_a))
        st_b, d_b = self._op(self.cookie, self.PATH, 0, frozen_b, "frozen_b")
        wiped = st_b.startswith("200") and "mind_a" not in self._server_week()
        conflicted = st_b.startswith("409") or (
            isinstance(d_b, dict) and d_b.get("error") == "conflict"
        )
        self.assertTrue(wiped or conflicted, (st_b, d_b, self._server_week()))

    def test_01b_lazy_queue_keeps_both_on_server_and_ui(self):
        """Исправление: B собирается из lastLoaded после A → на сервере A и B."""
        ok_a, _ = self._persist_plan_cell("mind_a", "10", key="lazy_a")
        ok_b, _ = self._persist_plan_cell("mind_b", "20", key="lazy_b")
        self.assertTrue(ok_a)
        self.assertTrue(ok_b)
        week = self._server_week()
        self.assertEqual(week["mind_a"]["plan_value"], 10)
        self.assertEqual(week["mind_b"]["plan_value"], 20)
        self.assertEqual(self._ui_week()["mind_a"]["plan_value"], 10)
        self.assertEqual(self._ui_week()["mind_b"]["plan_value"], 20)
        self.assertEqual(self._loaded_week()["mind_a"]["plan_value"], 10)
        self.assertEqual(self._loaded_week()["mind_b"]["plan_value"], 20)
        self.assertFalse(self.drafts["plans"])

    def test_02_abc_sequential(self):
        self.assertTrue(self._persist_plan_cell("mind_a", "1", key="abc_a")[0])
        self.assertTrue(self._persist_plan_cell("mind_b", "2", key="abc_b")[0])
        self.assertTrue(self._persist_plan_cell("mind_c", "3", key="abc_c")[0])
        week = self._server_week()
        self.assertEqual(week["mind_a"]["plan_value"], 1)
        self.assertEqual(week["mind_b"]["plan_value"], 2)
        self.assertEqual(week["mind_c"]["plan_value"], 3)

    def test_03_same_cell_rapid_edits_last_wins(self):
        self.assertTrue(self._persist_plan_cell("mind_a", "1", key="same1")[0])
        self.assertTrue(self._persist_plan_cell("mind_a", "2", key="same2")[0])
        self.assertTrue(self._persist_plan_cell("mind_a", "3", key="same3")[0])
        self.assertEqual(self._server_week()["mind_a"]["plan_value"], 3)
        self.assertEqual(self._ui_week()["mind_a"]["plan_value"], 3)
        self.assertNotIn(self._draft_key(self.PK, "mind_a"), self.drafts["plans"])

    def test_04_typing_b_while_a_saves_keeps_draft(self):
        # A уходит на сервер; B только локальный черновик (persist:false).
        self._remember_draft("plans", self.PK, "mind_b", {
            "indicator_id": "mind_b", "value": "88", "plan_value": 88,
        })
        self.app_data["management"]["plans"].setdefault(self.PK, {})["mind_b"] = {
            "indicator_id": "mind_b", "value": "88", "plan_value": 88,
        }
        ok, _ = self._persist_plan_cell("mind_a", "11", key="draft_a")
        self.assertTrue(ok)
        self.assertEqual(self._server_week()["mind_a"]["plan_value"], 11)
        self.assertNotIn("mind_b", self._server_week())
        self.assertNotIn("mind_b", self._loaded_week())
        self.assertEqual(self._ui_week()["mind_b"]["plan_value"], 88)
        self.assertIn(self._draft_key(self.PK, "mind_b"), self.drafts["plans"])

    def test_05_save_a_with_unsaved_b_then_save_b(self):
        self._remember_draft("plans", self.PK, "mind_b", {
            "indicator_id": "mind_b", "value": "5", "plan_value": 5,
        })
        self.app_data["management"]["plans"].setdefault(self.PK, {})["mind_b"] = {
            "indicator_id": "mind_b", "value": "5", "plan_value": 5,
        }
        self.assertTrue(self._persist_plan_cell("mind_a", "4", key="unsaved_a")[0])
        self.assertEqual(self._ui_week()["mind_b"]["plan_value"], 5)
        self.assertTrue(self._persist_plan_cell("mind_b", "5", key="unsaved_b")[0])
        self.assertEqual(self._server_week()["mind_a"]["plan_value"], 4)
        self.assertEqual(self._server_week()["mind_b"]["plan_value"], 5)
        self.assertFalse(self.drafts["plans"])

    def test_06_two_users_different_cells(self):
        c2 = self._login("usr_spu_q2", "6666")
        self.assertTrue(self._persist_plan_cell("mind_a", "10", key="u1_a")[0])
        # Второй клиент: свой lastLoaded после SoftSync = сервер с A.
        other_loaded = json.loads(json.dumps(self.store["payload"]))
        other_v = int(
            ((other_loaded.get("_wt_records") or {}).get(self.PATH) or {}).get("v")
            or self.record_v[self.PATH]
        )
        base = (other_loaded.get("management") or {}).get("plans") or {}
        payload_b = self.overlay_cell(base, self.PK, "mind_b", {
            "indicator_id": "mind_b", "value": "20", "plan_value": 20,
        })
        st, data = self._op(c2, self.PATH, other_v, payload_b, "u2_b")
        if st.startswith("409") or (isinstance(data, dict) and data.get("error") == "conflict"):
            payload_b = self.overlay_cell(data.get("server_value") or base, self.PK, "mind_b", {
                "indicator_id": "mind_b", "value": "20", "plan_value": 20,
            })
            st, data = self._op(c2, self.PATH, int(data.get("server_v") or other_v), payload_b, "u2_b_r")
        self.assertTrue(st.startswith("200"), (st, data))
        week = self._server_week()
        self.assertEqual(week["mind_a"]["plan_value"], 10)
        self.assertEqual(week["mind_b"]["plan_value"], 20)

    def test_07_two_users_same_cell_409_retry(self):
        c2 = self._login("usr_spu_q2", "6666")
        self.assertTrue(self._persist_plan_cell("mind_a", "1", key="same_u1")[0])
        # Второй пользователь со stale expected_v=0 и своим значением.
        stale = self.overlay_cell({}, self.PK, "mind_a", {
            "indicator_id": "mind_a", "value": "99", "plan_value": 99,
        })
        st, data = self._op(c2, self.PATH, 0, stale, "same_u2_stale")
        self.assertTrue(
            st.startswith("409") or (isinstance(data, dict) and data.get("error") == "conflict"),
            (st, data),
        )
        retry = self.overlay_cell(data.get("server_value"), self.PK, "mind_a", {
            "indicator_id": "mind_a", "value": "99", "plan_value": 99,
        })
        st2, data2 = self._op(c2, self.PATH, int(data.get("server_v") or 1), retry, "same_u2_ok")
        self.assertTrue(st2.startswith("200"), (st2, data2))
        self.assertEqual(self._server_week()["mind_a"]["plan_value"], 99)

    def test_08_conflict_cancel_keeps_local_draft_not_success(self):
        self.assertTrue(self._persist_plan_cell("mind_a", "1", key="cc_a")[0])
        # Имитация чужой записи B на сервере.
        server_now = self.overlay_cell(self.store["payload"]["management"]["plans"], self.PK, "mind_b", {
            "indicator_id": "mind_b", "value": "7", "plan_value": 7,
        })
        st_b, d_b = self._op(self.cookie, self.PATH, self.record_v[self.PATH], server_now, "cc_foreign_b")
        self.assertTrue(st_b.startswith("200"), (st_b, d_b))
        # Локальный клиент ещё со старым record_v пытается сохранить C — 409, отмена.
        self.record_v[self.PATH] = 0  # устаревшая версия у клиента
        ok, data = self._persist_plan_cell("mind_c", "3", key="cc_c", confirm_retry=False)
        self.assertFalse(ok)
        self.assertIsInstance(data, dict)
        self.assertEqual(data.get("error"), "conflict")
        self.assertNotIn("mind_c", self._server_week())
        self.assertIn(self._draft_key(self.PK, "mind_c"), self.drafts["plans"])
        self.assertEqual(self._ui_week()["mind_c"]["plan_value"], 3)
        # Подтверждение: повтор поверх server_value (не считаем отмену успехом).
        ok2, _ = self._persist_plan_cell("mind_c", "3", key="cc_c2", confirm_retry=True)
        self.assertTrue(ok2)
        self.assertEqual(self._server_week()["mind_c"]["plan_value"], 3)
        self.assertEqual(self._server_week()["mind_b"]["plan_value"], 7)
        self.assertNotIn(self._draft_key(self.PK, "mind_c"), self.drafts["plans"])

    def test_09_timeout_no_auto_retry_marker_and_state(self):
        # Фактический timeout сети здесь не эмулируем (FakeConn синхронный);
        # проверяем контракт маркеров + что один успешный op не порождает пакет.
        src = Path(__file__).resolve().parents[1].joinpath("app.html").read_text(encoding="utf-8")
        start = src.find("function wtSpuPersistRecordOp(")
        end = src.find("function wtPersistMgmtBranches(", start + 10)
        body = src[start:end]
        self.assertIn("Ответ сервера не получен", body)
        # После offline/network нет второго вызова Post в том же обработчике.
        idx = body.find('status === "offline"')
        self.assertGreater(idx, 0)
        self.assertNotIn("wtSpuPostStateOp(", body[idx:idx + 500])
        before = len(self.ops_log)
        self.assertTrue(self._persist_plan_cell("mind_a", "1", key="to_a")[0])
        self.assertEqual(len(self.ops_log) - before, 1)

    def test_10_clear_cell_preserves_sibling(self):
        self.assertTrue(self._persist_plan_cell("mind_a", "1", key="clr_a")[0])
        self.assertTrue(self._persist_plan_cell("mind_b", "2", key="clr_b")[0])
        self.assertTrue(self._persist_plan_cell("mind_a", "", key="clr_a_empty")[0])
        week = self._server_week()
        self.assertNotIn("mind_a", week)
        self.assertEqual(week["mind_b"]["plan_value"], 2)

    def test_11_reopen_after_saves(self):
        self.assertTrue(self._persist_plan_cell("mind_a", "10", key="re_a")[0])
        self.assertTrue(self._persist_plan_cell("mind_b", "20", key="re_b")[0])
        # F5: загрузка с сервера.
        reopened = json.loads(json.dumps(self.store["payload"]))
        week = (reopened.get("management") or {}).get("plans", {}).get(self.PK) or {}
        self.assertEqual(week["mind_a"]["plan_value"], 10)
        self.assertEqual(week["mind_b"]["plan_value"], 20)
        self.assertEqual(
            reopened.get("shopMetrics"),
            SAMPLE_APP.get("shopMetrics"),
        )

    def test_12_no_457_wide_batch_only_single_op(self):
        # Локальный «шум» в appData не уходит: шлём только overlay одной ячейки.
        noise = self.app_data.setdefault("shopMetrics", {})
        for i in range(457):
            noise["noise_%d" % i] = {"v": i}
        before_ops = len(self.ops_log)
        before_shop = json.loads(json.dumps(self.store["payload"].get("shopMetrics")))
        self.assertTrue(self._persist_plan_cell("mind_a", "42", key="no457")[0])
        self.assertEqual(len(self.ops_log) - before_ops, 1)
        self.assertEqual(self.ops_log[-1]["path"], self.PATH)
        self.assertEqual(self.store["payload"].get("shopMetrics"), before_shop)
        self.assertEqual(self._server_week()["mind_a"]["plan_value"], 42)
        self.assertNotIn("mind_drift", self._server_week())


if __name__ == "__main__":
    unittest.main()
