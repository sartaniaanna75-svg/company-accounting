# -*- coding: utf-8 -*-
"""Автотесты API хранилища и входа без PostgreSQL и без Timeweb."""

from __future__ import annotations

import json
import os
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
        self.assertEqual(data, {"ok": True})
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
            self.assertEqual(data, {"ok": True})
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
        stored_users = self.store["payload"]["users"]
        self.assertTrue(str(stored_users[0].get("pin_hash") or "").startswith("scrypt$"))
        self.assertNotIn("pin", stored_users[0])
        status2, got, raw, _ = call("GET", "/api/state", cookie=cookie)
        self.assertTrue(status2.startswith("200"))
        self.assertEqual(got["payload"]["workspaces"][0]["code"], "shop")
        self.assertNotIn("pin_hash", raw)

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
        self.assertIn("shopMetrics", data["payload"])

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
        status2, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(data["version"]),
                "payload": {"salary": {"2026-10": {"e_shop": {"issued": True, "rate": 1500}}}},
            },
            cookie=cookie,
        )
        self.assertTrue(status2.startswith("200"), status2)
        self.assertTrue(self.store["payload"]["salary"]["2026-10"]["e_shop"]["issued"])

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
        shop = next(w for w in self.store["payload"]["workspaces"] if w["code"] == "shop")
        self.assertEqual(shop["timesheet"]["2026-10"]["e_shop"]["1"], "О")
        self.assertEqual(shop["employees"][0]["rate"], 1500)
        self.assertEqual(self.store["payload"]["workspaces"][1], before_wh)
        self.assertEqual(self.store["payload"]["salary"], SAMPLE_APP["salary"])
        codes = [w["code"] for w in data["payload"]["workspaces"]]
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
        visible = data["payload"]["workspaces"]
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
        cases = [
            {"workspaces": [{"code": "warehouse", "employees": [{"id": "e_wh", "name": "Чужой"}]}]},
            {"workspaces": [{"code": "shop", "employees": [{"id": "e_shop", "name": "Касса", "rate": 1}]}]},
            {"workspaces": [{"code": "shop", "employees": [
                {"id": "e_shop", "name": "Касса", "employee_id": "e_other"},
                {"id": "e_new", "name": "Не должен появиться", "employee_id": "e_new", "rate": 900},
            ]}]},
            {"workspaces": [{"code": "shop", "employees": [{"id": "e_new", "name": "С пин", "pin": "9999"}]}]},
            {"users": [{"user_id": "usr_dir", "role": "admin", "pin": "0000"}]},
        ]
        for body in cases:
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
        self._seed_metric_access()
        cookie = self._add_shop_manager()
        status, *_ = call(
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
        access = self.store["payload"]["shopMetrics"]["workAccess"]["2026-10"]
        self.assertEqual(access["e_shop"], [])
        self.assertEqual(access["e_wh"], ["m_wh"])

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
        status, *_ = call(
            "PUT",
            "/api/state",
            {
                "version": int(self.store["version"]),
                "payload": {"financeRec": {
                    "removed_source_ids": ["acc_shop"],
                    "sources": [{
                        "finance_source_id": "fsrc_shop_a",
                        "account_id": "acc_shop",
                        "name": "Не должно сохраниться",
                        "object_id": "farea_shop",
                        "account_type": "cash",
                        "sort_order": 70,
                    }],
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
        self.assertIn("wtThreeWayMerge", save)
        self.assertIn("payloadForServerPut", save)
        self.assertIn("buildStaffPutPayload", save)
        access_start = src.find("function saveAccessUser(")
        access_end = src.find("function renderSummaryUsers(", access_start)
        access = src[access_start:access_end]
        self.assertGreater(access_start, 0)
        self.assertGreater(access_end, access_start)
        self.assertIn("done.then", access)
        self.assertIn("if (ok) afterSaved()", access)
        self.assertIn("if (pin) prev.pin = pin", access)

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


if __name__ == "__main__":
    unittest.main()
