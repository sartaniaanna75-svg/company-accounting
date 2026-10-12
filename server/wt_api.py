#!/usr/bin/env python3
"""Хранилище appData и серверный вход по существующим appData.users. Интерфейс пока не подключается."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import time
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any
from wsgiref.simple_server import make_server

MAX_BODY = 20 * 1024 * 1024
STATE_ID = 1
REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC_MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
}
LOGIN_WINDOW_SEC = 15 * 60
LOGIN_MAX_FAILS = 5
LOGIN_FAILS: dict[str, list[float]] = {}
BOOTSTRAP_USER_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,80}$")
ADMIN_PERM_KEYS = (
    "timesheet",
    "metricsInput",
    "metricsSetup",
    "revision",
    "salesPlan",
    "discipline",
    "salary",
    "salaryCalc",
    "salaryPay",
    "salary1c",
    "vacationPlan",
    "kpi",
    "settings",
    "management",
    "managementIndicators",
    "managementStructure",
    "managementTasks",
    "managementMeetings",
    "orgStructure",
    "financeRec",
    "financeRecSetup",
    "customerReturns",
    "customerReturnsPicker",
    "customerReturnsRecount",
    "customerReturnsGive",
    "customerReturnsRebill",
    "customerReturnsDriver",
)
BOOTSTRAP_DEPARTMENTS = ("shop", "office", "warehouse", "drivers", "summary")


def _env(name: str, default: str = "") -> str:
    return str(os.environ.get(name, default) or default)


def bind_host() -> str:
    return _env("WT_BIND", "127.0.0.1")


def bind_port() -> int:
    try:
        return int(_env("WT_PORT", "8780"))
    except ValueError:
        return 8780


def dsn() -> str:
    url = _env("DATABASE_URL")
    if url:
        return url
    host = _env("PGHOST", "127.0.0.1")
    port = _env("PGPORT", "5432")
    name = _env("PGDATABASE", "worktime")
    user = _env("PGUSER", "worktime")
    password = _env("PGPASSWORD")
    return (
        f"host={host} port={port} dbname={name} user={user} "
        f"password={password}"
    )


def db_configured() -> bool:
    return bool(_env("DATABASE_URL") or _env("PGPASSWORD") or _env("PGUSER"))


class PinPepperMissing(RuntimeError):
    """WT_PIN_PEPPER не задан. Не использовать пустой pepper."""


def pin_pepper_ready() -> bool:
    return bool(_env("WT_PIN_PEPPER"))


def pin_pepper() -> bytes:
    if not pin_pepper_ready():
        raise PinPepperMissing()
    return _env("WT_PIN_PEPPER").encode("utf-8")


def auth_unavailable():
    return json_bytes({"error": "authentication unavailable"}, "503 Service Unavailable")


def scrypt_n() -> int:
    try:
        n = int(_env("WT_SCRYPT_N") or "16384")
    except ValueError:
        n = 16384
    return n if n >= 16 else 16384


def session_seconds() -> int:
    try:
        hours = float(_env("WT_SESSION_HOURS") or "12")
    except ValueError:
        hours = 12
    return max(300, int(hours * 3600))


def static_root() -> Path:
    return Path(_env("WT_STATIC_ROOT") or str(REPO_ROOT)).resolve()


def connect():
    import psycopg

    conn = psycopg.connect(dsn(), autocommit=False, connect_timeout=5)
    return conn


def db_unavailable():
    return json_bytes({"error": "database unavailable"}, "503 Service Unavailable")


def json_bytes(data: Any, status: str = "200 OK", extra: list | None = None):
    body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
    headers = [
        ("Content-Type", "application/json; charset=utf-8"),
        ("Content-Length", str(len(body))),
        ("Cache-Control", "no-store"),
    ]
    if extra:
        headers.extend(extra)
    return status, headers, body


def read_body(environ) -> bytes:
    try:
        size = int(environ.get("CONTENT_LENGTH") or 0)
    except (TypeError, ValueError):
        size = 0
    if size < 0 or size > MAX_BODY:
        raise ValueError("body too large")
    if size == 0:
        return b""
    return environ["wsgi.input"].read(size)


def hash_pin(pin: str) -> str:
    if not pin_pepper_ready():
        raise PinPepperMissing()
    salt = secrets.token_bytes(16)
    n = scrypt_n()
    dk = hashlib.scrypt(
        pin.encode("utf-8"),
        salt=salt + pin_pepper(),
        n=n,
        r=8,
        p=1,
        dklen=32,
    )
    return "scrypt$%s$8$1$%s$%s" % (
        n,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(dk).decode("ascii"),
    )


def verify_pin(pin: str, stored_hash: str) -> bool:
    if not pin_pepper_ready():
        return False
    parts = str(stored_hash or "").split("$")
    if len(parts) != 6 or parts[0] != "scrypt":
        return False
    try:
        n = int(parts[1])
        r = int(parts[2])
        p = int(parts[3])
        salt = base64.b64decode(parts[4])
        expected = base64.b64decode(parts[5])
        dk = hashlib.scrypt(
            pin.encode("utf-8"),
            salt=salt + pin_pepper(),
            n=n,
            r=r,
            p=p,
            dklen=len(expected) or 32,
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk, expected)


def pins_match_user(user: dict, pin: str) -> tuple[bool, bool]:
    if not pin_pepper_ready():
        return False, False
    pin = str(pin or "").strip()
    if not pin or not user:
        return False, False
    stored_hash = str(user.get("pin_hash") or "")
    if stored_hash:
        return verify_pin(pin, stored_hash), False
    stored_pin = str(user.get("pin") or user.get("password") or "").strip()
    if stored_pin and hmac.compare_digest(pin, stored_pin):
        return True, True
    return False, False


def hash_pins_in_payload(payload: dict, previous: dict | None = None) -> dict:
    if not isinstance(payload, dict):
        return payload
    users = payload.get("users")
    if not isinstance(users, list):
        return payload
    prev_by_id: dict[str, dict] = {}
    if isinstance(previous, dict):
        for item in previous.get("users") or []:
            if isinstance(item, dict) and item.get("user_id"):
                prev_by_id[str(item.get("user_id"))] = item
    for user in users:
        if not isinstance(user, dict):
            continue
        raw = str(user.get("pin") or user.get("password") or "").strip()
        if raw:
            user["pin_hash"] = hash_pin(raw)
        elif not str(user.get("pin_hash") or ""):
            old = prev_by_id.get(str(user.get("user_id") or ""))
            if old and old.get("pin_hash"):
                user["pin_hash"] = old["pin_hash"]
        user.pop("pin", None)
        user.pop("password", None)
    return payload


def admin_permissions() -> dict[str, str]:
    perms = {key: "edit" for key in ADMIN_PERM_KEYS}
    perms["kpi"] = "manage"
    return perms


def payload_has_users(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    users = payload.get("users")
    return isinstance(users, list) and len(users) > 0


def bootstrap_admin_record(display_name: str, pin: str, user_id: str) -> dict:
    return {
        "user_id": user_id,
        "display_name": display_name,
        "role": "admin",
        "legacy_role": "admin",
        "is_active": True,
        "departments": list(BOOTSTRAP_DEPARTMENTS),
        "permissions": admin_permissions(),
        "employee_id": "",
        "pin_hash": hash_pin(pin),
    }


def merge_bootstrap_user(payload: Any, user: dict) -> tuple[dict | None, str | None]:
    current = _clone(payload) if isinstance(payload, dict) else {}
    if payload_has_users(current):
        return None, "users_exist"
    current["users"] = [user]
    return current, None


def bootstrap_config_from_env() -> tuple[dict | None, str | None]:
    if not pin_pepper_ready():
        return None, "WT_PIN_PEPPER is required"
    name = _env("WT_BOOTSTRAP_NAME").strip()
    pin = _env("WT_BOOTSTRAP_PIN").strip()
    user_id = _env("WT_BOOTSTRAP_USER_ID").strip() or "user_admin"
    if not name:
        return None, "WT_BOOTSTRAP_NAME is required"
    if not pin:
        return None, "WT_BOOTSTRAP_PIN is required"
    if len(pin) < 4:
        return None, "WT_BOOTSTRAP_PIN is too short"
    if not BOOTSTRAP_USER_ID_RE.match(user_id):
        return None, "WT_BOOTSTRAP_USER_ID is invalid"
    return {"display_name": name, "pin": pin, "user_id": user_id}, None


def public_user(user: dict | None) -> dict | None:
    if not user:
        return None
    return {
        "user_id": user.get("user_id") or "",
        "display_name": user.get("display_name") or user.get("name") or "",
        "role": user.get("role") or "user",
        "legacy_role": user.get("legacy_role") or "",
        "departments": list(user.get("departments") or []),
        "permissions": dict(user.get("permissions") or {}),
        "is_active": user.get("is_active") is not False,
        "employee_id": user.get("employee_id") or "",
    }


def strip_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if key in ("pin", "pin_hash", "password", "rolePins"):
                continue
            out[key] = strip_secrets(item)
        return out
    if isinstance(value, list):
        return [strip_secrets(item) for item in value]
    return value


def is_app_admin(user: dict | None) -> bool:
    if not user or user.get("is_active") is False:
        return False
    return user.get("role") == "admin" or user.get("legacy_role") == "admin"


def perm_rank(value: Any) -> int:
    if value == "manage":
        return 3
    if value == "edit":
        return 2
    if value == "view":
        return 1
    return 0


def user_has_perm(user: dict | None, key: str, minimum: str = "view") -> bool:
    if is_app_admin(user):
        return True
    if not user:
        return False
    need = perm_rank(minimum)
    perms = user.get("permissions") or {}
    if key == "salary":
        if perm_rank(perms.get("salary")) >= need:
            return True
        if need <= 1 and (
            perm_rank(perms.get("salaryCalc")) > 0
            or perm_rank(perms.get("salaryPay")) > 0
            or perm_rank(perms.get("salary1c")) > 0
        ):
            return True
        if need <= 2 and perm_rank(perms.get("salaryCalc")) > 0:
            return True
        return False
    if key in ("metrics", "metricsInput"):
        return max(
            perm_rank(perms.get("metricsInput")),
            perm_rank(perms.get("metrics")),
        ) >= need
    return perm_rank(perms.get(key)) >= need


def find_user(payload: dict | None, user_id: str) -> dict | None:
    if not payload or not user_id:
        return None
    users = payload.get("users")
    if not isinstance(users, list):
        return None
    for user in users:
        if isinstance(user, dict) and str(user.get("user_id") or "") == user_id:
            return user
    return None


def can_read_full_state(user: dict | None) -> bool:
    return is_app_admin(user)


def can_write_full_state(user: dict | None) -> bool:
    if is_app_admin(user):
        return True
    return user_has_perm(user, "settings", "edit")


def json_canon(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def public_users_list(users: Any) -> list:
    if not isinstance(users, list):
        return []
    return [public_user(item) for item in users if isinstance(item, dict)]


def incoming_users_forbidden(incoming_users: Any, current_users: Any) -> bool:
    if incoming_users is None:
        return False
    if not isinstance(incoming_users, list):
        return True
    for item in incoming_users:
        if not isinstance(item, dict):
            return True
        if str(item.get("pin") or item.get("password") or item.get("pin_hash") or "").strip():
            return True
    return json_canon(public_users_list(incoming_users)) != json_canon(public_users_list(current_users))


def _clone(value: Any) -> Any:
    return json.loads(json.dumps(value))


def _same(left: Any, right: Any) -> bool:
    return json_canon(left) == json_canon(right)


def _find_workspace(workspaces: Any, code: str) -> dict | None:
    if not isinstance(workspaces, list):
        return None
    for item in workspaces:
        if isinstance(item, dict) and str(item.get("code") or "") == code:
            return item
    return None


def _is_closed_key(key: str, allow_salary: bool) -> bool:
    if key in SECRET_FIELD_KEYS:
        return True
    if allow_salary:
        return False
    return key in SALARY_FIELD_KEYS or key == "financeRec"


def _list_item_key(item: dict) -> str | None:
    for cand in ("id", "employee_id", "user_id", "code"):
        if item.get(cand) not in (None, ""):
            return cand
    return None


def _match_list_item(current: list, item: dict, index: int) -> Any:
    key_name = _list_item_key(item)
    if key_name:
        needle = str(item.get(key_name))
        for row in current:
            if isinstance(row, dict) and str(row.get(key_name) or "") == needle:
                return row
        return None
    if 0 <= index < len(current):
        return current[index]
    return None


def _closed_field_violations(current: Any, incoming: Any, allow_salary: bool = False) -> bool:
    if isinstance(incoming, dict):
        cur = current if isinstance(current, dict) else {}
        for key, val in incoming.items():
            if _is_closed_key(str(key), allow_salary):
                if not _same(val, cur.get(key)):
                    return True
                continue
            if _closed_field_violations(cur.get(key), val, allow_salary):
                return True
        return False
    if isinstance(incoming, list):
        cur_list = current if isinstance(current, list) else []
        for index, item in enumerate(incoming):
            if isinstance(item, dict):
                matched = _match_list_item(cur_list, item, index)
                if _closed_field_violations(matched, item, allow_salary):
                    return True
            elif not _same(item, cur_list[index] if index < len(cur_list) else None):
                if isinstance(item, (dict, list)) and _closed_field_violations(
                    cur_list[index] if index < len(cur_list) else None, item, allow_salary
                ):
                    return True
        return False
    return False


def _overlay_open(current: Any, incoming: Any, allow_salary: bool = False) -> Any:
    if isinstance(incoming, dict):
        base = _clone(current) if isinstance(current, dict) else {}
        for key, val in incoming.items():
            if _is_closed_key(str(key), allow_salary):
                continue
            base[key] = _overlay_open(base.get(key), val, allow_salary)
        return base
    if isinstance(incoming, list):
        cur_list = _clone(current) if isinstance(current, list) else []
        if incoming and isinstance(incoming[0], dict):
            for index, item in enumerate(incoming):
                if not isinstance(item, dict):
                    continue
                key_name = _list_item_key(item)
                replaced = False
                if key_name:
                    needle = str(item.get(key_name))
                    for row_i, row in enumerate(cur_list):
                        if isinstance(row, dict) and str(row.get(key_name) or "") == needle:
                            cur_list[row_i] = _overlay_open(row, item, allow_salary)
                            replaced = True
                            break
                elif index < len(cur_list):
                    cur_list[index] = _overlay_open(cur_list[index], item, allow_salary)
                    replaced = True
                if not replaced:
                    cur_list.append(_overlay_open({}, item, allow_salary))
            return cur_list
        return _clone(incoming)
    return _clone(incoming)


def _can_write_top(user: dict, key: str) -> bool:
    codes = user_dept_codes(user)
    if key == "shopMetrics":
        return "shop" in codes and (
            user_has_perm(user, "metricsInput", "edit") or user_has_perm(user, "metrics", "edit")
        )
    if key == "shopRevision":
        return "shop" in codes and user_has_perm(user, "revision", "edit")
    if key == "shopSalesPlan":
        return "shop" in codes and user_has_perm(user, "salesPlan", "edit")
    if key == "shopMetricCatalog":
        return "shop" in codes and (
            user_has_perm(user, "metricsSetup", "edit") or user_has_perm(user, "kpi", "edit")
        )
    if key == "shopPayroll":
        return "shop" in codes and user_has_perm(user, "salary", "edit")
    if key == "financeRec":
        return user_has_perm(user, "financeRec", "edit")
    if key == "management":
        return user_has_perm(user, "management", "edit")
    if key == "orgStructure":
        return user_has_perm(user, "orgStructure", "edit")
    if key == "customerReturns":
        return user_has_perm(user, "customerReturns", "edit")
    return False


def _can_write_dept_map(user: dict, key: str, dept: str) -> bool:
    codes = user_dept_codes(user)
    if dept not in codes:
        return False
    if key == "deptMetrics":
        return user_has_perm(user, "metricsInput", "edit") or user_has_perm(user, "metrics", "edit")
    if key == "deptRevisions":
        return user_has_perm(user, "revision", "edit")
    if key == "deptSalesPlans":
        return user_has_perm(user, "salesPlan", "edit")
    return False


def _empty_wipe(val: Any, existing: Any) -> bool:
    if val is None:
        return True
    if isinstance(val, (dict, list)) and not val and existing:
        return True
    return False


def _merge_admin_payload(current: dict, incoming: dict) -> dict:
    stored = _clone(current)
    for key, val in incoming.items():
        if key == "workspaces":
            if not isinstance(val, list) or not val:
                continue
            current_ws = stored.get("workspaces")
            if not isinstance(current_ws, list):
                stored["workspaces"] = _clone(val)
                continue
            index_by_code = {
                str(item.get("code") or ""): idx
                for idx, item in enumerate(current_ws)
                if isinstance(item, dict) and item.get("code")
            }
            for item in val:
                if not isinstance(item, dict) or not item.get("code"):
                    continue
                code = str(item.get("code"))
                if code in index_by_code:
                    dest = current_ws[index_by_code[code]]
                    if not isinstance(dest, dict):
                        current_ws[index_by_code[code]] = _clone(item)
                        continue
                    for ws_key, ws_val in item.items():
                        if _empty_wipe(ws_val, dest.get(ws_key)):
                            continue
                        dest[ws_key] = _clone(ws_val)
                else:
                    current_ws.append(_clone(item))
            stored["workspaces"] = current_ws
        elif key == "users":
            if not isinstance(val, list) or not val:
                continue
            current_users = stored.get("users")
            if not isinstance(current_users, list) or not current_users:
                stored["users"] = _clone(val)
                continue
            index_by_id = {
                str(item.get("user_id") or ""): idx
                for idx, item in enumerate(current_users)
                if isinstance(item, dict) and item.get("user_id")
            }
            for item in val:
                if not isinstance(item, dict) or not item.get("user_id"):
                    continue
                uid = str(item.get("user_id"))
                if uid in index_by_id:
                    current_users[index_by_id[uid]] = _clone(item)
                else:
                    current_users.append(_clone(item))
            stored["users"] = current_users
        elif _empty_wipe(val, stored.get(key)):
            continue
        else:
            stored[key] = _clone(val)
    return hash_pins_in_payload(stored, current)


def _staff_put_forbidden(current: dict, incoming: dict, user: dict) -> bool:
    codes = user_dept_codes(user)
    for key, val in incoming.items():
        if key == "workspaces":
            if not isinstance(val, list):
                return True
            for in_ws in val:
                if not isinstance(in_ws, dict):
                    return True
                code = str(in_ws.get("code") or "")
                if not code or code not in codes:
                    return True
                cur_ws = _find_workspace(current.get("workspaces"), code)
                if not cur_ws:
                    return True
                for field, field_val in in_ws.items():
                    if field in ("id", "code", "name", "type"):
                        if field in cur_ws and str(field_val) != str(cur_ws.get(field)):
                            return True
                        continue
                    if field == "timesheet":
                        if _closed_field_violations(cur_ws.get("timesheet"), field_val, False):
                            return True
                        if not user_has_perm(user, "timesheet", "edit") and not _same(
                            field_val, cur_ws.get("timesheet")
                        ):
                            return True
                        continue
                    if not _same(field_val, cur_ws.get(field)):
                        return True
            continue
        if key in ("deptMetrics", "deptRevisions", "deptSalesPlans"):
            if not isinstance(val, dict):
                return True
            current_map = current.get(key) if isinstance(current.get(key), dict) else {}
            for dept, branch in val.items():
                dept_code = str(dept)
                allow_sal = user_has_perm(user, "salary", "edit")
                if not _can_write_dept_map(user, key, dept_code):
                    if not _same(branch, current_map.get(dept_code)):
                        return True
                    continue
                if _closed_field_violations(current_map.get(dept_code), branch, allow_sal):
                    return True
            continue
        if _can_write_top(user, key):
            allow_sal = user_has_perm(user, "salary", "edit") or key == "financeRec"
            if _closed_field_violations(current.get(key), val, allow_sal):
                return True
            continue
        if not _same(val, current.get(key)):
            return True
    return False


def _apply_staff_writes(current: dict, incoming: dict, user: dict) -> dict:
    stored = _clone(current)
    if isinstance(incoming.get("workspaces"), list) and user_has_perm(user, "timesheet", "edit"):
        for in_ws in incoming.get("workspaces") or []:
            if not isinstance(in_ws, dict) or "timesheet" not in in_ws:
                continue
            code = str(in_ws.get("code") or "")
            cur_ws = _find_workspace(stored.get("workspaces"), code)
            if cur_ws is not None:
                cur_ws["timesheet"] = _overlay_open(cur_ws.get("timesheet"), in_ws.get("timesheet"), False)
    for key in (
        "shopMetrics",
        "shopRevision",
        "shopSalesPlan",
        "shopMetricCatalog",
        "shopPayroll",
        "financeRec",
        "management",
        "orgStructure",
        "customerReturns",
    ):
        if key in incoming and _can_write_top(user, key):
            allow_sal = user_has_perm(user, "salary", "edit") or key == "financeRec"
            stored[key] = _overlay_open(stored.get(key), incoming.get(key), allow_sal)
    for key in ("deptMetrics", "deptRevisions", "deptSalesPlans"):
        if key not in incoming or not isinstance(incoming.get(key), dict):
            continue
        if not isinstance(stored.get(key), dict):
            stored[key] = {}
        allow_sal = user_has_perm(user, "salary", "edit")
        for dept, branch in incoming[key].items():
            if _can_write_dept_map(user, key, str(dept)):
                stored[key][str(dept)] = _overlay_open(stored[key].get(str(dept)), branch, allow_sal)
    return stored


def prepare_put_payload(current: dict, incoming: dict, user: dict) -> tuple[dict | None, str | None]:
    """Пишем поверх снимка в БД. Запрещённое поле — 403, без частичной записи."""
    if not isinstance(current, dict):
        current = {}
    if not isinstance(incoming, dict):
        return None, "forbidden"
    if is_app_admin(user):
        return _merge_admin_payload(current, incoming), None
    if _staff_put_forbidden(current, incoming, user):
        return None, "forbidden"
    return _apply_staff_writes(current, incoming, user), None


def payload_for_client(user: dict | None, payload: dict) -> dict:
    safe = strip_secrets(payload)
    if is_app_admin(user):
        return safe
    out = dict(safe) if isinstance(safe, dict) else {}
    out.pop("users", None)
    if not user_has_perm(user, "salary", "view"):
        out.pop("salary", None)
    if not user_has_perm(user, "financeRec", "view"):
        out.pop("financeRec", None)
    return out


SHOP_CODES = frozenset({"shop"})
TERRITORY_CODES = frozenset({"office", "warehouse", "drivers"})
SALARY_FIELD_KEYS = frozenset(
    {
        "salary",
        "salaries",
        "rate",
        "rates",
        "taxiDaily",
        "taxi",
        "payroll",
        "shopPayroll",
        "vacationPlans",
        "advance",
        "issued",
        "payout",
        "pay",
        "terms",
        "salaryCalc",
        "salaryPay",
        "salary1c",
    }
)
SECRET_FIELD_KEYS = frozenset({"pin", "pin_hash", "password", "rolePins", "roleUsers"})


def user_dept_codes(user: dict | None) -> set[str]:
    codes: set[str] = set()
    if not user:
        return codes
    for raw in user.get("departments") or []:
        code = str(raw or "").strip()
        if code == "territory":
            codes.update(TERRITORY_CODES)
        elif code:
            codes.add(code)
    return codes


def drop_closed_fields(value: Any, allow_salary: bool, allow_finance: bool) -> Any:
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if key in SECRET_FIELD_KEYS:
                continue
            if key in SALARY_FIELD_KEYS and not allow_salary:
                continue
            if key == "financeRec" and not allow_finance:
                continue
            out[key] = drop_closed_fields(item, allow_salary, allow_finance)
        return out
    if isinstance(value, list):
        return [drop_closed_fields(item, allow_salary, allow_finance) for item in value]
    return value


def filter_workspace(workspace: dict, allowed_codes: set[str], user: dict) -> dict | None:
    code = str(workspace.get("code") or "")
    if code not in allowed_codes:
        return None
    allow_salary = user_has_perm(user, "salary", "view")
    allow_finance = user_has_perm(user, "financeRec", "view")
    out: dict[str, Any] = {}
    for key, item in workspace.items():
        if key in SECRET_FIELD_KEYS:
            continue
        if key in SALARY_FIELD_KEYS and not allow_salary:
            continue
        if key == "employees" and isinstance(item, list):
            out[key] = [drop_closed_fields(emp, allow_salary, allow_finance) for emp in item]
            continue
        if key == "timesheet":
            if user_has_perm(user, "timesheet", "view"):
                out[key] = drop_closed_fields(item, allow_salary, allow_finance)
            else:
                out[key] = {}
            continue
        out[key] = drop_closed_fields(item, allow_salary, allow_finance)
    return out


def pick_dept_branch(value: Any, allowed_codes: set[str], allow_salary: bool, allow_finance: bool) -> dict:
    if not isinstance(value, dict):
        return {}
    return {
        key: drop_closed_fields(item, allow_salary, allow_finance)
        for key, item in value.items()
        if str(key) in allowed_codes
    }


def filter_state_payload(payload: dict, user: dict) -> dict:
    """Сотруднику — только понятные разрешённые ветки. Неизвестное не отдаём."""
    allow_salary = user_has_perm(user, "salary", "view")
    allow_finance = user_has_perm(user, "financeRec", "view")
    codes = user_dept_codes(user)
    out: dict[str, Any] = {}
    workspaces = payload.get("workspaces")
    if isinstance(workspaces, list):
        filtered = []
        for item in workspaces:
            if isinstance(item, dict):
                kept = filter_workspace(item, codes, user)
                if kept:
                    filtered.append(kept)
        out["workspaces"] = filtered

    can_metrics = (
        user_has_perm(user, "metricsInput", "view")
        or user_has_perm(user, "metricsSetup", "view")
        or user_has_perm(user, "metrics", "view")
    )
    if "shop" in codes:
        if can_metrics and "shopMetrics" in payload:
            out["shopMetrics"] = drop_closed_fields(payload.get("shopMetrics"), allow_salary, allow_finance)
        if user_has_perm(user, "revision", "view") and "shopRevision" in payload:
            out["shopRevision"] = drop_closed_fields(payload.get("shopRevision"), allow_salary, allow_finance)
        if user_has_perm(user, "salesPlan", "view") and "shopSalesPlan" in payload:
            out["shopSalesPlan"] = drop_closed_fields(payload.get("shopSalesPlan"), allow_salary, allow_finance)
        if (
            user_has_perm(user, "metricsSetup", "view") or user_has_perm(user, "kpi", "view")
        ) and "shopMetricCatalog" in payload:
            out["shopMetricCatalog"] = drop_closed_fields(
                payload.get("shopMetricCatalog"), allow_salary, allow_finance
            )
        if allow_salary and "shopPayroll" in payload:
            out["shopPayroll"] = drop_closed_fields(payload.get("shopPayroll"), True, allow_finance)

    territory = codes & TERRITORY_CODES
    if territory:
        if can_metrics:
            branch = pick_dept_branch(payload.get("deptMetrics"), territory, allow_salary, allow_finance)
            if branch:
                out["deptMetrics"] = branch
        if user_has_perm(user, "revision", "view"):
            branch = pick_dept_branch(payload.get("deptRevisions"), territory, allow_salary, allow_finance)
            if branch:
                out["deptRevisions"] = branch
        if user_has_perm(user, "salesPlan", "view"):
            branch = pick_dept_branch(payload.get("deptSalesPlans"), territory, allow_salary, allow_finance)
            if branch:
                out["deptSalesPlans"] = branch

    if allow_finance and "financeRec" in payload:
        out["financeRec"] = drop_closed_fields(payload.get("financeRec"), allow_salary, True)
    if user_has_perm(user, "management", "view") and "management" in payload:
        out["management"] = drop_closed_fields(payload.get("management"), allow_salary, allow_finance)
    if user_has_perm(user, "orgStructure", "view") and "orgStructure" in payload:
        out["orgStructure"] = drop_closed_fields(payload.get("orgStructure"), allow_salary, allow_finance)
    if user_has_perm(user, "customerReturns", "view") and "customerReturns" in payload:
        out["customerReturns"] = drop_closed_fields(payload.get("customerReturns"), allow_salary, allow_finance)
    return out


def directory_users(payload: dict | None) -> list[dict]:
    rows = []
    if not payload or not isinstance(payload.get("users"), list):
        return rows
    for user in payload.get("users") or []:
        if not isinstance(user, dict) or user.get("is_active") is False:
            continue
        user_id = str(user.get("user_id") or "").strip()
        if not user_id:
            continue
        depts = user.get("departments") if isinstance(user.get("departments"), list) else []
        rows.append(
            {
                "user_id": user_id,
                "display_name": str(user.get("display_name") or user.get("name") or ""),
                "departments": [str(item) for item in depts],
            }
        )
    return rows


def conflict_body(latest: dict) -> dict:
    return {
        "error": "conflict",
        "message": "Данные изменил другой сотрудник. Обновите снимок.",
        "version": latest.get("version") or 0,
        "updated_at": latest.get("updated_at") or "",
        "updated_by": latest.get("updated_by") or "",
    }


def cookie_secure() -> bool:
    return _env("WT_COOKIE_SECURE").strip().lower() in ("1", "true", "yes", "on")


def cookie_header(raw_token: str, max_age: int) -> str:
    parts = [
        "wt_session=%s" % raw_token,
        "Path=/",
        "HttpOnly",
        "SameSite=Lax",
        "Max-Age=%s" % max_age,
    ]
    if cookie_secure():
        parts.append("Secure")
    return "; ".join(parts)


def clear_cookie_header() -> str:
    parts = ["wt_session=", "Path=/", "HttpOnly", "SameSite=Lax", "Max-Age=0"]
    if cookie_secure():
        parts.append("Secure")
    return "; ".join(parts)


def read_session_token(environ) -> str:
    cookie = SimpleCookie()
    raw_cookie = environ.get("HTTP_COOKIE") or ""
    if raw_cookie:
        try:
            cookie.load(raw_cookie)
        except (TypeError, ValueError):
            cookie = SimpleCookie()
        morsel = cookie.get("wt_session")
        if morsel and morsel.value:
            return str(morsel.value)
    auth = str(environ.get("HTTP_AUTHORIZATION") or "")
    prefix = "session "
    if auth.lower().startswith(prefix):
        return auth[len(prefix):].strip()
    return ""


def token_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _as_utc(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    return None


def insert_session(cur, token_h: str, user_id: str, expires_at: datetime) -> None:
    cur.execute(
        """
        INSERT INTO app_sessions (token_hash, user_id, expires_at)
        VALUES (%s, %s, %s)
        """,
        (token_h, user_id, expires_at),
    )


def create_session(user_id: str) -> tuple[str, int]:
    if not db_configured():
        raise RuntimeError("database unavailable")
    raw = secrets.token_urlsafe(32)
    ttl = session_seconds()
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl)
    conn = connect()
    try:
        with conn.cursor() as cur:
            insert_session(cur, token_hash(raw), user_id, expires_at)
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()
    return raw, ttl


def drop_session(raw: str) -> None:
    if not raw:
        return
    if not db_configured():
        raise RuntimeError("database unavailable")
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM app_sessions WHERE token_hash = %s",
                (token_hash(raw),),
            )
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


def lookup_session_user_id(raw: str) -> str:
    if not raw:
        return ""
    if not db_configured():
        raise RuntimeError("database unavailable")
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT user_id, expires_at FROM app_sessions WHERE token_hash = %s",
                (token_hash(raw),),
            )
            row = cur.fetchone()
            if not row:
                conn.commit()
                return ""
            user_id, expires_at = row
            exp = _as_utc(expires_at)
            if not exp or exp <= datetime.now(timezone.utc):
                cur.execute(
                    "DELETE FROM app_sessions WHERE token_hash = %s",
                    (token_hash(raw),),
                )
                conn.commit()
                return ""
            conn.commit()
            return str(user_id or "")
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


def session_user_id(environ) -> str:
    raw = read_session_token(environ)
    if not raw:
        return ""
    return lookup_session_user_id(raw)


def require_session(environ):
    raw = read_session_token(environ)
    if not raw:
        return "", json_bytes({"error": "unauthorized"}, "401 Unauthorized")
    try:
        user_id = lookup_session_user_id(raw)
    except Exception:
        return "", db_unavailable()
    if not user_id:
        return "", json_bytes({"error": "unauthorized"}, "401 Unauthorized")
    return user_id, None


def client_key(environ, user_id: str) -> str:
    ip = str(environ.get("HTTP_X_FORWARDED_FOR") or environ.get("REMOTE_ADDR") or "local")
    ip = ip.split(",")[0].strip()
    return ip + "|" + user_id


def login_blocked(key: str) -> bool:
    now = time.time()
    hits = [t for t in LOGIN_FAILS.get(key, []) if now - t < LOGIN_WINDOW_SEC]
    LOGIN_FAILS[key] = hits
    return len(hits) >= LOGIN_MAX_FAILS


def register_login_fail(key: str) -> None:
    LOGIN_FAILS.setdefault(key, []).append(time.time())


def clear_login_fails(key: str) -> None:
    LOGIN_FAILS.pop(key, None)


def handle_static(path: str):
    rel = "index.html" if path in ("", "/") else path.lstrip("/")
    if ".." in rel.replace("\\", "/").split("/"):
        return json_bytes({"error": "not found"}, "404 Not Found")
    root = static_root()
    candidate = (root / rel).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return json_bytes({"error": "not found"}, "404 Not Found")
    suffix = candidate.suffix.lower()
    if suffix not in STATIC_MIME:
        return json_bytes({"error": "not found"}, "404 Not Found")
    allowed_parents = {root, (root / "css").resolve()}
    if candidate.parent.resolve() not in allowed_parents or not candidate.is_file():
        return json_bytes({"error": "not found"}, "404 Not Found")
    body = candidate.read_bytes()
    headers = [
        ("Content-Type", STATIC_MIME[suffix]),
        ("Content-Length", str(len(body))),
        ("Cache-Control", "no-store"),
    ]
    return "200 OK", headers, body


def row_to_state(row) -> dict:
    payload, version, updated_at, updated_by = row
    return {
        "version": int(version or 0),
        "payload": payload if payload is not None else {},
        "updated_at": updated_at.isoformat() if updated_at else "",
        "updated_by": updated_by or "",
    }


def fetch_state(cur) -> dict:
    cur.execute(
        "SELECT payload, version, updated_at, updated_by FROM app_state WHERE id = %s",
        (STATE_ID,),
    )
    row = cur.fetchone()
    if not row:
        return {"version": 0, "payload": None, "updated_at": "", "updated_by": ""}
    return row_to_state(row)


def persist_payload(cur, payload: dict, expected_version: int, updated_by: str):
    cur.execute(
        """
        INSERT INTO app_state (id, payload, version, updated_by)
        VALUES (%s, '{}'::jsonb, 0, '')
        ON CONFLICT (id) DO NOTHING
        """,
        (STATE_ID,),
    )
    cur.execute(
        """
        UPDATE app_state
        SET payload = %s::jsonb,
            version = version + 1,
            updated_at = NOW(),
            updated_by = %s
        WHERE id = %s AND version = %s
        RETURNING payload, version, updated_at, updated_by
        """,
        (json.dumps(payload, ensure_ascii=False), updated_by, STATE_ID, expected_version),
    )
    return cur.fetchone()


def persist_bootstrap_admin(cur, payload: dict, expected_version: int, updated_by: str):
    cur.execute(
        """
        INSERT INTO app_state (id, payload, version, updated_by)
        VALUES (%s, '{}'::jsonb, 0, '')
        ON CONFLICT (id) DO NOTHING
        """,
        (STATE_ID,),
    )
    cur.execute(
        """
        UPDATE app_state
        SET payload = %s::jsonb,
            version = version + 1,
            updated_at = NOW(),
            updated_by = %s
        WHERE id = %s AND version = %s
          AND (
            NOT (payload ? 'users')
            OR jsonb_typeof(payload->'users') <> 'array'
            OR jsonb_array_length(COALESCE(payload->'users', '[]'::jsonb)) = 0
          )
        RETURNING payload, version, updated_at, updated_by
        """,
        (json.dumps(payload, ensure_ascii=False), updated_by, STATE_ID, expected_version),
    )
    return cur.fetchone()


def run_bootstrap(connect_fn=None) -> tuple[int, str]:
    cfg, err = bootstrap_config_from_env()
    if err:
        return 2, err
    if not db_configured():
        return 1, "database unavailable"
    opener = connect_fn or connect
    try:
        conn = opener()
    except Exception:
        return 1, "database unavailable"
    try:
        with conn.cursor() as cur:
            state = fetch_state(cur)
            payload = state.get("payload") if state else {}
            try:
                user = bootstrap_admin_record(cfg["display_name"], cfg["pin"], cfg["user_id"])
            except PinPepperMissing:
                conn.rollback()
                return 2, "WT_PIN_PEPPER is required"
            merged, reason = merge_bootstrap_user(payload, user)
            if reason or merged is None:
                conn.rollback()
                return 3, "bootstrap refused: users already exist"
            row = persist_bootstrap_admin(
                cur, merged, int(state.get("version") or 0), cfg["user_id"]
            )
            if not row:
                conn.rollback()
                return 3, "bootstrap refused: users already exist"
            conn.commit()
        return 0, "bootstrap ok user_id=%s" % cfg["user_id"]
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return 1, "database unavailable"
    finally:
        try:
            conn.close()
        except Exception:
            pass


def run_bootstrap_cli() -> int:
    code, message = run_bootstrap()
    print(message, flush=True)
    return code


def handle_health():
    # Живость процесса для Timeweb: без PostgreSQL, без авторизации, без редиректов.
    return json_bytes({"ok": True})


def load_state_or_error():
    if not db_configured():
        return None, db_unavailable()
    try:
        conn = connect()
    except Exception:
        return None, db_unavailable()
    try:
        with conn.cursor() as cur:
            state = fetch_state(cur)
        conn.commit()
        return state, None
    except Exception:
        return None, db_unavailable()
    finally:
        conn.close()


def current_user_from_state(environ, payload: dict | None, user_id: str | None = None):
    uid = user_id if user_id is not None else session_user_id(environ)
    if not uid:
        return None
    user = find_user(payload, uid)
    if not user or user.get("is_active") is False:
        return None
    return user


def handle_login(environ):
    raw = read_body(environ)
    try:
        data = json.loads(raw.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return json_bytes({"error": "invalid json"}, "400 Bad Request")
    if not isinstance(data, dict):
        return json_bytes({"error": "body must be an object"}, "400 Bad Request")
    user_id = str(data.get("user_id") or "").strip()
    pin = str(data.get("pin") or "").strip()
    if not user_id or not pin:
        return json_bytes({"error": "invalid login"}, "400 Bad Request")
    if not pin_pepper_ready():
        return auth_unavailable()
    key = client_key(environ, user_id)
    if login_blocked(key):
        return json_bytes({"error": "too many attempts"}, "429 Too Many Requests")
    state, err = load_state_or_error()
    if err:
        return err
    payload = state.get("payload") if state else None
    if not isinstance(payload, dict):
        payload = {}
    user = find_user(payload, user_id)
    ok, needs_hash = pins_match_user(user or {}, pin) if user else (False, False)
    dummy = hash_pin(pin)
    if not user or user.get("is_active") is False or not ok:
        register_login_fail(key)
        _ = dummy
        return json_bytes({"error": "invalid login"}, "401 Unauthorized")
    clear_login_fails(key)
    if needs_hash:
        user["pin_hash"] = hash_pin(pin)
        user.pop("pin", None)
        user.pop("password", None)
        try:
            conn = connect()
            try:
                with conn.cursor() as cur:
                    persist_payload(cur, payload, int(state.get("version") or 0), user_id)
                conn.commit()
            finally:
                conn.close()
        except Exception:
            pass
    try:
        token, ttl = create_session(user_id)
    except Exception:
        return db_unavailable()
    extra = [("Set-Cookie", cookie_header(token, ttl))]
    return json_bytes({"user": public_user(user)}, extra=extra)


def handle_logout(environ):
    extra = [("Set-Cookie", clear_cookie_header())]
    raw = read_session_token(environ)
    if not raw:
        return json_bytes({"ok": True}, extra=extra)
    try:
        drop_session(raw)
    except Exception:
        return json_bytes({"error": "database unavailable"}, "503 Service Unavailable", extra)
    return json_bytes({"ok": True}, extra=extra)


def handle_directory():
    state, err = load_state_or_error()
    if err:
        return err
    payload = state.get("payload") if state else None
    if not isinstance(payload, dict):
        payload = {}
    return json_bytes({"users": directory_users(payload)})


def handle_me(environ):
    user_id, err = require_session(environ)
    if err:
        return err
    state, err = load_state_or_error()
    if err:
        return err
    payload = state.get("payload") if state else None
    if not isinstance(payload, dict):
        payload = {}
    user = current_user_from_state(environ, payload, user_id)
    if not user:
        return json_bytes({"error": "unauthorized"}, "401 Unauthorized")
    return json_bytes({"user": public_user(user)})


def handle_get_state(environ):
    user_id, err = require_session(environ)
    if err:
        return err
    state, err = load_state_or_error()
    if err:
        return err
    payload = state.get("payload") if state else None
    if not isinstance(payload, dict):
        payload = {}
    user = current_user_from_state(environ, payload, user_id)
    if not user:
        return json_bytes({"error": "unauthorized"}, "401 Unauthorized")
    safe = dict(state)
    if is_app_admin(user):
        safe["payload"] = strip_secrets(payload)
    else:
        safe["payload"] = filter_state_payload(payload, user)
    return json_bytes(safe)


def handle_put_state(environ):
    raw = read_body(environ)
    try:
        data = json.loads(raw.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return json_bytes({"error": "invalid json"}, "400 Bad Request")
    if not isinstance(data, dict):
        return json_bytes({"error": "body must be an object"}, "400 Bad Request")
    if "version" not in data:
        return json_bytes({"error": "version is required"}, "400 Bad Request")
    try:
        expected = int(data["version"])
    except (TypeError, ValueError):
        return json_bytes({"error": "version must be an integer"}, "400 Bad Request")
    payload = data.get("payload")
    if payload is None:
        return json_bytes({"error": "payload is required"}, "400 Bad Request")
    if not isinstance(payload, dict):
        return json_bytes({"error": "payload must be an object (appData)"}, "400 Bad Request")
    user_id, err = require_session(environ)
    if err:
        return err

    state, err = load_state_or_error()
    if err:
        return err
    current_payload = state.get("payload") if state else None
    if not isinstance(current_payload, dict):
        current_payload = {}
    user = current_user_from_state(environ, current_payload, user_id)
    if not user:
        return json_bytes({"error": "unauthorized"}, "401 Unauthorized")
    try:
        current_version = int(state.get("version") or 0)
    except (TypeError, ValueError):
        current_version = 0
    if expected != current_version:
        return json_bytes(conflict_body(state), "409 Conflict")
    try:
        stored, denied = prepare_put_payload(current_payload, payload, user)
    except PinPepperMissing:
        return auth_unavailable()
    if denied or stored is None:
        return json_bytes({"error": "forbidden"}, "403 Forbidden")
    updated_by = str(user.get("user_id") or "")[:120]
    if not db_configured():
        return db_unavailable()
    try:
        conn = connect()
    except Exception:
        return db_unavailable()
    try:
        with conn.cursor() as cur:
            row = persist_payload(cur, stored, expected, updated_by)
            if row:
                conn.commit()
                result = row_to_state(row)
                stored_payload = result.get("payload") or {}
                if is_app_admin(user):
                    result["payload"] = strip_secrets(stored_payload)
                else:
                    result["payload"] = filter_state_payload(stored_payload, user)
                return json_bytes(result)
            latest = fetch_state(cur)
            conn.rollback()
            return json_bytes(conflict_body(latest), "409 Conflict")
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return db_unavailable()
    finally:
        conn.close()


def application(environ, start_response):
    method = (environ.get("REQUEST_METHOD") or "GET").upper()
    path = environ.get("PATH_INFO") or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")

    try:
        if method in ("GET", "HEAD") and path == "/api/health":
            status, headers, body = handle_health()
            if method == "HEAD":
                body = b""
        elif method == "POST" and path == "/api/login":
            status, headers, body = handle_login(environ)
        elif method == "POST" and path == "/api/logout":
            status, headers, body = handle_logout(environ)
        elif method == "GET" and path == "/api/me":
            status, headers, body = handle_me(environ)
        elif method == "GET" and path == "/api/directory":
            status, headers, body = handle_directory()
        elif method == "GET" and path == "/api/state":
            status, headers, body = handle_get_state(environ)
        elif method == "PUT" and path == "/api/state":
            status, headers, body = handle_put_state(environ)
        elif method == "OPTIONS" and path.startswith("/api/"):
            status, headers, body = "204 No Content", [("Content-Length", "0")], b""
        elif method == "GET":
            status, headers, body = handle_static(path)
        else:
            status, headers, body = json_bytes({"error": "not found"}, "404 Not Found")
    except ValueError as err:
        msg = str(err)
        if msg == "body too large":
            status, headers, body = json_bytes({"error": msg}, "400 Bad Request")
        else:
            status, headers, body = json_bytes({"error": "bad request"}, "400 Bad Request")
    except Exception:
        status, headers, body = json_bytes({"error": "server error"}, "500 Internal Server Error")

    start_response(status, headers)
    return [body]


def main(argv: list[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["bootstrap"]:
        raise SystemExit(run_bootstrap_cli())
    host = bind_host()
    port = bind_port()
    print(f"WorkTime store API http://{host}:{port}/api/health (UI not connected)", flush=True)
    httpd = make_server(host, port, application)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
