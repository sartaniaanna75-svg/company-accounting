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
        if "SELECT user_id, expires_at FROM app_sessions" in text:
            rec = (self.store.get("sessions") or {}).get(params[0])
            self._row = (rec["user_id"], rec["expires_at"]) if rec else None
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
        self.assertEqual(self.store.get("sessions"), {})

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
        self.assertEqual(set(data["users"][0].keys()), {"user_id", "display_name", "departments"})
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

    def test_source_has_no_bootstrap_pin(self):
        src = Path(__file__).with_name("wt_api.py").read_text(encoding="utf-8")
        self.assertIn('args[:1] == ["bootstrap"]', src)
        self.assertNotRegex(src, r"WT_BOOTSTRAP_PIN\s*=")
        self.assertNotIn("5555", src)
        self.assertNotIn(self.PIN, src)


if __name__ == "__main__":
    unittest.main()
