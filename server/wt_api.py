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
LOGIN_FAIL_USER_PREFIX = "wt.login_fail:"
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


def pin_secrets_equal(left: str, right: str) -> bool:
    if not pin_pepper_ready():
        return False
    key = pin_pepper()
    left_d = hmac.new(key, left.encode("utf-8"), hashlib.sha256).digest()
    right_d = hmac.new(key, right.encode("utf-8"), hashlib.sha256).digest()
    return hmac.compare_digest(left_d, right_d)


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


def user_can_open_management(user: dict | None) -> bool:
    if is_app_admin(user):
        return True
    if not user:
        return False
    for key in (
        "management",
        "managementIndicators",
        "managementStructure",
        "managementTasks",
        "managementMeetings",
    ):
        if user_has_perm(user, key, "view"):
            return True
    return False


MGMT_KEY_PERMS = {
    "indicators": "managementIndicators",
    "plans": "managementIndicators",
    "manualFacts": "managementIndicators",
    "input_order": "managementIndicators",
    "spuOrgNodes": "managementStructure",
    "spuDepartments": "managementStructure",
    "tasks": "managementTasks",
    "meetings": "managementMeetings",
    "events": "managementMeetings",
}


def _can_write_mgmt_key(user: dict, key: str) -> bool:
    if user_has_perm(user, "management", "edit"):
        return True
    perm = MGMT_KEY_PERMS.get(str(key))
    if not perm:
        return False
    if perm == "managementStructure":
        return user_has_perm(user, "managementStructure", "edit") or user_has_perm(
            user, "managementIndicators", "edit"
        )
    return user_has_perm(user, perm, "edit")


def _can_write_any_management(user: dict | None) -> bool:
    if not user:
        return False
    if user_has_perm(user, "management", "edit"):
        return True
    for perm in (
        "managementIndicators",
        "managementStructure",
        "managementTasks",
        "managementMeetings",
    ):
        if user_has_perm(user, perm, "edit"):
            return True
    return False


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


def can_write_dept_employees(user: dict | None) -> bool:
    """Руководитель отдела может вести карточки только своего подразделения."""
    if not user or user.get("is_active") is False or is_app_admin(user):
        return False
    if user.get("role") != "manager" and user.get("legacy_role") != "manager":
        return False
    return user_has_perm(user, "timesheet", "edit")


# Префиксы ID только для НОВЫХ сотрудников. Старые emp_* не трогаем.
EMPLOYEE_ID_PREFIX_BY_DEPT = {
    "shop": "V",
    "warehouse": "T-S",
    "office": "T-O",
    "drivers": "T-L",
}
EMPLOYEE_ID_NUM_RE = re.compile(
    r"^(?P<prefix>V|T-S|T-O|T-L)-(?P<num>\d{1,8})$",
    re.IGNORECASE,
)


def employee_id_prefix_for_dept(dept_code: str) -> str | None:
    return EMPLOYEE_ID_PREFIX_BY_DEPT.get(str(dept_code or "").strip())


def format_new_employee_id(prefix: str, number: int) -> str:
    return f"{prefix}-{int(number):06d}"


def _iter_workspace_employees(payload: Any):
    if not isinstance(payload, dict):
        return
    for ws in payload.get("workspaces") or []:
        if not isinstance(ws, dict) or ws.get("type") == "summary":
            continue
        code = str(ws.get("code") or "").strip()
        if not code:
            continue
        for emp in ws.get("employees") or []:
            if isinstance(emp, dict):
                yield code, emp


def collect_employee_id_dept_counts(payload: Any) -> dict[str, dict[str, int]]:
    """id → {dept_code: count} по всем подразделениям."""
    out: dict[str, dict[str, int]] = {}
    for code, emp in _iter_workspace_employees(payload):
        eid = str(emp.get("id") or "").strip()
        if not eid:
            continue
        bucket = out.setdefault(eid, {})
        bucket[code] = int(bucket.get(code) or 0) + 1
    return out


def max_employee_id_number_for_prefix(payload: Any, prefix: str) -> int:
    """Максимум номера по префиксу: карточки + employeeIdCounters."""
    pref = str(prefix or "").upper()
    max_n = 0
    counters = payload.get("employeeIdCounters") if isinstance(payload, dict) else None
    if isinstance(counters, dict):
        raw = counters.get(pref)
        if raw is None:
            raw = counters.get(prefix)
        try:
            max_n = max(max_n, int(raw or 0))
        except (TypeError, ValueError):
            pass
    for _code, emp in _iter_workspace_employees(payload):
        eid = str(emp.get("id") or "").strip()
        m = EMPLOYEE_ID_NUM_RE.match(eid)
        if not m:
            continue
        if m.group("prefix").upper() != pref:
            continue
        try:
            max_n = max(max_n, int(m.group("num")))
        except (TypeError, ValueError):
            continue
    return max_n


def allocate_employee_id_in_payload(payload: dict, dept_code: str) -> tuple[str | None, str | None]:
    """
    Атомарно (при вызове под FOR UPDATE) резервирует следующий ID в payload.employeeIdCounters.
    Возвращает (employee_id, error).
    """
    prefix = employee_id_prefix_for_dept(dept_code)
    if not prefix:
        return None, "invalid department"
    next_n = max_employee_id_number_for_prefix(payload, prefix) + 1
    eid = format_new_employee_id(prefix, next_n)
    # Глобальная уникальность: id не должен уже существовать (любой формат).
    existing = collect_employee_id_dept_counts(payload)
    if eid in existing:
        next_n = max_employee_id_number_for_prefix(payload, prefix) + 1
        while format_new_employee_id(prefix, next_n) in existing:
            next_n += 1
        eid = format_new_employee_id(prefix, next_n)
    counters = payload.get("employeeIdCounters")
    if not isinstance(counters, dict):
        counters = {}
    else:
        counters = dict(counters)
    counters[prefix] = next_n
    payload["employeeIdCounters"] = counters
    return eid, None


def _effective_employee_id_counts_after_put(current: dict, incoming: dict) -> dict[str, dict[str, int]]:
    """
    Счётчики id после PUT: для отделов из incoming берём их employees,
    остальные отделы оставляем из current (staff шлёт неполный payload).
    """
    effective = collect_employee_id_dept_counts(current)
    touched: set[str] = set()
    if not isinstance(incoming, dict):
        return effective
    for ws in incoming.get("workspaces") or []:
        if not isinstance(ws, dict) or ws.get("type") == "summary":
            continue
        code = str(ws.get("code") or "").strip()
        if not code or "employees" not in ws:
            continue
        touched.add(code)
    if not touched:
        return effective
    # Обнуляем вклады touched-отделов, затем набираем из incoming.
    for eid, depts in list(effective.items()):
        for code in touched:
            if code in depts:
                depts[code] = 0
        if sum(depts.values()) <= 0:
            del effective[eid]
    for ws in incoming.get("workspaces") or []:
        if not isinstance(ws, dict) or ws.get("type") == "summary":
            continue
        code = str(ws.get("code") or "").strip()
        if code not in touched:
            continue
        for emp in ws.get("employees") or []:
            if not isinstance(emp, dict):
                continue
            eid = str(emp.get("id") or "").strip()
            if not eid:
                continue
            bucket = effective.setdefault(eid, {})
            bucket[code] = int(bucket.get(code) or 0) + 1
    return effective


def employee_id_put_denied(current: dict, incoming: dict) -> str | None:
    """
    Блокирует только НОВЫЕ нарушения уникальности ID.
    Исторические дубли (effective_total <= cur_total) не мешают обычному сохранению.
    """
    cur = collect_employee_id_dept_counts(current)
    eff = _effective_employee_id_counts_after_put(current, incoming)
    for eid in set(cur) | set(eff):
        cur_total = sum((cur.get(eid) or {}).values())
        eff_total = sum((eff.get(eid) or {}).values())
        if eff_total <= cur_total:
            continue
        if cur_total == 0 and eff_total == 1:
            continue
        return "duplicate_employee_id"
    return None


def merge_employee_id_counters(current: Any, incoming: Any) -> dict:
    """Счётчики только растут (max), чтобы устаревший снимок админа не откатил резерв."""
    out: dict[str, int] = {}
    for src in (current, incoming):
        if not isinstance(src, dict):
            continue
        for key, raw in src.items():
            pref = str(key or "").strip()
            if not pref:
                continue
            try:
                n = int(raw or 0)
            except (TypeError, ValueError):
                continue
            if n < 0:
                continue
            out[pref] = max(int(out.get(pref) or 0), n)
    return out


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
    """Слияние словарей. Явный JSON null удаляет ключ; отсутствующий ключ не трогает."""
    if isinstance(incoming, dict):
        base = _clone(current) if isinstance(current, dict) else {}
        for key, val in incoming.items():
            if _is_closed_key(str(key), allow_salary):
                continue
            if val is None:
                base.pop(key, None)
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


# Рабочий ввод по вкладкам магазина (месяц → сотрудник → день). Не настройки прав.
METRIC_CATEGORY_STORE_KEYS = frozenset({"cashiers", "pickers", "managers"})
METRIC_INPUT_KEYS = frozenset(
    {
        "facts",
        "channelValues",
        "store",
        "channels",
        "recountResponsible",
        "inventoryResults",
        "dayRoles",
        "entries",
    }
) | METRIC_CATEGORY_STORE_KEYS
METRIC_SETUP_KEYS = frozenset({"workAccess", "extraAccess"})
# Служебные/админские ключи shopMetrics — staff не должен их менять через PUT.
METRIC_ADMIN_META_KEYS = frozenset({"store_id", "storeMeta", "substitutionPayDefaults"})
# Денежные поля KPI в shopPayroll — только системный администратор.
SHOP_PAYROLL_KPI_MONEY_KEYS = frozenset({"kpiDefs", "assignments"})
# Категория территории → подразделение табеля (как в app.html TERRITORY_METRIC_CATEGORIES).
TERRITORY_CATEGORY_DEPT = {
    "office": "office",
    "warehouse": "warehouse",
    "drivers": "drivers",
    "sales_opt": "drivers",
    "sales_retail": "warehouse",
    "common": "",
    "returns": "",
}
FINANCE_DATA_KEYS = (
    "operations",
    "money_transits",
    "openings",
    "day_closings",
    "period_states",
    "audit",
    "import_batches",
    "formulas",
)
FINANCE_SOURCE_FIELDS = frozenset(
    {
        "name",
        "account_type",
        "sort_order",
        "is_active",
        "in_actual_balance",
        "revenue_direction",
        "storage_account_id",
        "shop_block",
        "external_id",
    }
)
FINANCE_ACCOUNT_TYPES = frozenset({"cash", "bank", "card", "in_transit", "other"})
SHOP_METRIC_FALLBACK = "shop"


def _can_edit_metric_input(user: dict) -> bool:
    return user_has_perm(user, "metricsInput", "edit") or user_has_perm(user, "metrics", "edit")


def _can_edit_metric_setup(user: dict) -> bool:
    """Назначение сотрудников на метрики. Не связано с денежным KPI."""
    return user_has_perm(user, "metricsSetup", "edit")


def _employee_dept_map(payload: dict) -> dict[str, str]:
    found: dict[str, str] = {}
    for ws in payload.get("workspaces") or []:
        if not isinstance(ws, dict):
            continue
        code = str(ws.get("code") or "")
        for emp in ws.get("employees") or []:
            if isinstance(emp, dict) and emp.get("id") not in (None, ""):
                found[str(emp.get("id"))] = code
    return found


def _metric_id_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, bool) or item is None:
            continue
        text = str(item).strip()
        if text:
            out.append(text)
    return out


def _merge_access_map(current: Any, incoming: Any, allowed_ids: set[str] | None, nested: bool) -> Any:
    """Обновляет списки назначений. Ключи, которых нет во входящем снимке, не удаляет.

    allowed_ids=None — без фильтра (админ). Пустой список [] сохраняется как явный отказ
    от метрик (не путать с отсутствием ключа, когда действует наследование).
    Явный JSON null у сотрудника/месяца удаляет ключ.
    """
    base = _clone(current) if isinstance(current, dict) else {}
    if not isinstance(incoming, dict):
        return base
    if not nested:
        for emp_id, metrics in incoming.items():
            eid = str(emp_id)
            if allowed_ids is not None and eid not in allowed_ids:
                continue
            if metrics is None:
                base.pop(eid, None)
            else:
                base[eid] = _metric_id_list(metrics)
        return base
    for month, emps in incoming.items():
        month_key = str(month)
        if emps is None:
            base.pop(month_key, None)
            continue
        if not isinstance(emps, dict):
            continue
        slot = _clone(base.get(month_key)) if isinstance(base.get(month_key), dict) else {}
        for emp_id, metrics in emps.items():
            eid = str(emp_id)
            if allowed_ids is not None and eid not in allowed_ids:
                continue
            if metrics is None:
                slot.pop(eid, None)
            else:
                slot[eid] = _metric_id_list(metrics)
        base[month_key] = slot
    return base


def _merge_metric_branch_admin(current: Any, incoming: Any) -> Any:
    """Админский merge ветки метрик: факты/кассы через overlay, назначения — отдельно."""
    base = _clone(current) if isinstance(current, dict) else {}
    if not isinstance(incoming, dict):
        return base
    data_in = {key: val for key, val in incoming.items() if key not in METRIC_SETUP_KEYS}
    if data_in:
        base = _overlay_open(base, data_in, True)
    if "workAccess" in incoming:
        base["workAccess"] = _merge_access_map(base.get("workAccess"), incoming.get("workAccess"), None, True)
    if "extraAccess" in incoming:
        base["extraAccess"] = _merge_access_map(base.get("extraAccess"), incoming.get("extraAccess"), None, False)
    return base


def _metric_root_forbidden(current: Any, incoming: Any, user: dict) -> bool:
    if not isinstance(incoming, dict):
        return True
    cur = current if isinstance(current, dict) else {}
    can_input = _can_edit_metric_input(user)
    can_setup = _can_edit_metric_setup(user)
    for key, val in incoming.items():
        if key in METRIC_ADMIN_META_KEYS:
            if not _same(val, cur.get(key)):
                return True
            continue
        if key in METRIC_SETUP_KEYS:
            if not can_setup and not _same(val, cur.get(key)):
                return True
            continue
        if key in METRIC_INPUT_KEYS:
            if not can_input and not _same(val, cur.get(key)):
                return True
            if _closed_field_violations(cur.get(key), val, False):
                return True
            continue
        if not _same(val, cur.get(key)):
            return True
    return False


def _apply_metric_root(current: Any, incoming: Any, user: dict, dept_code: str, emp_dept: dict[str, str]) -> Any:
    """Накладывает только разрешённые ветки. Ключи, которых нет во входящем снимке, не удаляет."""
    base = _clone(current) if isinstance(current, dict) else {}
    if not isinstance(incoming, dict):
        return base
    allowed = {emp_id for emp_id, code in emp_dept.items() if code == dept_code}
    if _can_edit_metric_input(user):
        for key in METRIC_INPUT_KEYS:
            if key in incoming:
                base[key] = _overlay_open(base.get(key), incoming.get(key), False)
    if _can_edit_metric_setup(user):
        if "workAccess" in incoming:
            base["workAccess"] = _merge_access_map(base.get("workAccess"), incoming.get("workAccess"), allowed, True)
        if "extraAccess" in incoming:
            base["extraAccess"] = _merge_access_map(base.get("extraAccess"), incoming.get("extraAccess"), allowed, False)
    return base


def _metric_item_dept(item: dict) -> str:
    """Подразделение показателя. territory_category без department_id → известный dept."""
    if not isinstance(item, dict):
        return SHOP_METRIC_FALLBACK
    dept = str(item.get("department_id") or "").strip()
    if dept:
        return dept
    cat = str(item.get("territory_category") or "").strip()
    if cat in TERRITORY_CATEGORY_DEPT:
        return TERRITORY_CATEGORY_DEPT[cat]
    return SHOP_METRIC_FALLBACK


def _user_can_touch_metric_item(item: dict, codes: set[str]) -> bool:
    """Можно ли править показатель в рамках departments пользователя."""
    if not codes:
        return False
    dept = _metric_item_dept(item)
    if dept:
        return dept in codes
    # Общие / без dept: только назначения по разрешённым подразделениям.
    cat = str(item.get("territory_category") or "").strip()
    if cat in ("common", "returns") or not dept:
        if codes & TERRITORY_CODES:
            return True
        for row in item.get("assignments") or []:
            if isinstance(row, dict) and str(row.get("department_id") or "").strip() in codes:
                return True
    return False


def _is_territory_assignment_dept(dept: str) -> bool:
    """Назначение Территории: department_id ∈ office|warehouse|drivers (не shop)."""
    return str(dept or "").strip() in TERRITORY_CODES


def _merge_metric_assignments(
    current_rows: Any, incoming_rows: Any, codes: set[str], emp_dept: dict[str, str]
) -> list:
    result = _clone(current_rows) if isinstance(current_rows, list) else []
    result = [row for row in result if isinstance(row, dict)]
    index = {
        str(row.get("assignment_id")): pos
        for pos, row in enumerate(result)
        if row.get("assignment_id") not in (None, "")
    }
    if not isinstance(incoming_rows, list):
        return result
    for row in incoming_rows:
        if not isinstance(row, dict):
            continue
        dept = str(row.get("department_id") or "").strip()
        if dept not in codes:
            continue
        eids = [
            str(emp_id)
            for emp_id in _metric_id_list(row.get("employee_ids"))
            if emp_dept.get(str(emp_id)) == dept
        ]
        aid = str(row.get("assignment_id") or "").strip()
        if aid and aid in index:
            dest = result[index[aid]]
            dest_dept = str(dest.get("department_id") or "").strip()
            if dest_dept not in codes:
                continue
            dest["employee_ids"] = eids
            if row.get("assign_mode") in ("specific", "group_all"):
                dest["assign_mode"] = row.get("assign_mode")
            # Территория: сохранять должность и рабочую группу назначения.
            # Магазин Володарского (department_id=shop): прежнее поведение — только ids/mode.
            if _is_territory_assignment_dept(dest_dept) and _is_territory_assignment_dept(dept):
                if "position_id" in row:
                    dest["position_id"] = str(row.get("position_id") or "")
                work_tab = str(row.get("work_tab_id") or "").strip()
                if work_tab:
                    dest["work_tab_id"] = work_tab
            continue
        if not aid:
            aid = "masg_%d" % (len(result) + 1)
        created = _clone(row)
        created["assignment_id"] = aid
        created["department_id"] = dept
        created["employee_ids"] = eids
        result.append(created)
        index[aid] = len(result) - 1
    return result


def _apply_metric_catalog(current: Any, incoming: Any, user: dict, emp_dept: dict[str, str]) -> Any:
    base = _clone(current) if isinstance(current, dict) else {}
    codes = user_dept_codes(user)
    items = base.get("items") if isinstance(base.get("items"), list) else []
    items = [row for row in items if isinstance(row, dict)]
    by_id = {str(row.get("id")): pos for pos, row in enumerate(items) if row.get("id") not in (None, "")}
    incoming_items = incoming.get("items") if isinstance(incoming, dict) and isinstance(incoming.get("items"), list) else []
    for item in incoming_items:
        if not isinstance(item, dict) or item.get("id") in (None, ""):
            continue
        iid = str(item.get("id"))
        existing = items[by_id[iid]] if iid in by_id else None
        probe = item if _user_can_touch_metric_item(item, codes) else None
        if probe is None and existing is not None and _user_can_touch_metric_item(existing, codes):
            probe = existing
        if probe is None:
            continue
        dept = _metric_item_dept(item) or (_metric_item_dept(existing) if existing else "")
        patch = _clone(item)
        patch.pop("assignments", None)
        if existing is not None:
            if not _user_can_touch_metric_item(existing, codes):
                continue
            # Для общих показателей без dept не переписываем чужие поля целиком — только назначения.
            if not _metric_item_dept(existing) and not dept:
                merged = _clone(existing)
            else:
                merged = _overlay_open(existing, patch, False)
                merged["id"] = existing.get("id")
                if existing.get("department_id"):
                    merged["department_id"] = existing.get("department_id")
                elif dept:
                    merged["department_id"] = dept
            merged["assignments"] = _merge_metric_assignments(
                existing.get("assignments"), item.get("assignments"), codes, emp_dept
            )
            items[by_id[iid]] = merged
            continue
        if not dept and not _user_can_touch_metric_item(item, codes):
            continue
        created = _overlay_open({}, patch, False)
        created["id"] = iid
        if dept:
            created["department_id"] = dept
        created["assignments"] = _merge_metric_assignments([], item.get("assignments"), codes, emp_dept)
        items.append(created)
        by_id[iid] = len(items) - 1
    base["items"] = items
    return base


def _catalog_forbidden(current: Any, incoming: Any, user: dict) -> bool:
    if not isinstance(incoming, dict):
        return True
    if not _can_edit_metric_setup(user):
        return not _same(incoming, current)
    cur_items = current.get("items") if isinstance(current, dict) and isinstance(current.get("items"), list) else []
    by_id = {
        str(row.get("id")): row
        for row in cur_items
        if isinstance(row, dict) and row.get("id") not in (None, "")
    }
    codes = user_dept_codes(user)
    for item in incoming.get("items") or []:
        if not isinstance(item, dict) or item.get("id") in (None, ""):
            continue
        existing = by_id.get(str(item.get("id")))
        if not _user_can_touch_metric_item(item, codes):
            if existing is None or not _user_can_touch_metric_item(existing, codes):
                continue
        if _closed_field_violations(existing, item, False):
            return True
    return False


def _shop_payroll_kpi_money_forbidden(current: Any, incoming: Any, user: dict) -> bool:
    """Денежные KPI (kpiDefs / assignments) меняет только администратор."""
    if is_app_admin(user):
        return False
    if not isinstance(incoming, dict):
        return True
    cur = current if isinstance(current, dict) else {}
    for key in SHOP_PAYROLL_KPI_MONEY_KEYS:
        if key in incoming and not _same(incoming.get(key), cur.get(key)):
            return True
    return False


def _stored_finance_area_ids(finance: Any) -> set[str]:
    """Объекты Финконтроля из справочника, без привязки к подразделениям табеля."""
    found: set[str] = set()
    if not isinstance(finance, dict):
        return found
    for row in finance.get("areas") or []:
        if not isinstance(row, dict):
            continue
        area_id = str(row.get("object_id") or row.get("finance_area_id") or "").strip()
        if area_id:
            found.add(area_id)
    if found:
        return found
    for row in finance.get("sources") or []:
        if isinstance(row, dict):
            area_id = _source_area(row)
            if area_id:
                found.add(area_id)
    return found


def _user_finance_areas(user: dict, finance: Any = None) -> set[str]:
    if not user_has_perm(user, "financeRecSetup", "edit"):
        return set()
    return _stored_finance_area_ids(finance)


def _source_area(source: dict) -> str:
    return str(source.get("object_id") or source.get("finance_area_id") or "").strip()


def _source_id(source: dict) -> str:
    return str(source.get("finance_source_id") or source.get("account_id") or "").strip()


def _normalize_finance_sources(sources: Any) -> list:
    """Ставит устойчивый id, не меняя account_id, на который уже ссылаются операции."""
    rows = _clone(sources) if isinstance(sources, list) else []
    used: set[str] = set()
    for row in rows:
        if isinstance(row, dict):
            sid = _source_id(row)
            if sid:
                used.add(sid)
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        sid = str(row.get("finance_source_id") or "").strip()
        aid = str(row.get("account_id") or "").strip()
        if not sid and aid:
            sid = aid
        if not sid:
            number = index + 1
            sid = "fsrc_legacy_%d" % number
            while sid in used:
                number += 1
                sid = "fsrc_legacy_%d" % number
        if not aid:
            aid = sid
        row["finance_source_id"] = sid
        row["account_id"] = aid
        if not _source_area(row) and row.get("finance_area_id"):
            row["object_id"] = row.get("finance_area_id")
        used.add(sid)
    return [row for row in rows if isinstance(row, dict)]


def _finance_account_used(finance: dict, area: str, account_id: str) -> bool:
    aid = str(account_id or "").strip()
    if not aid:
        return False

    def hit(row: dict, keys: tuple[str, ...]) -> bool:
        row_area = str(row.get("object_id") or row.get("finance_area_id") or "")
        if area and row_area and row_area != area:
            return False
        return any(str(row.get(key) or "") == aid for key in keys)

    for row in finance.get("operations") or []:
        if isinstance(row, dict) and hit(
            row, ("account_id", "finance_source_id", "source_id", "account_to_id", "finance_source_to_id")
        ):
            return True
    for row in finance.get("openings") or []:
        if isinstance(row, dict) and hit(row, ("account_id", "finance_source_id")):
            try:
                amount = abs(float(row.get("value") or 0))
            except (TypeError, ValueError):
                amount = 0
            if row.get("is_manual") or amount >= 0.005:
                return True
    for row in finance.get("day_closings") or []:
        if isinstance(row, dict) and hit(row, ("account_id", "finance_source_id")):
            if row.get("value") not in (None, ""):
                return True
    for row in finance.get("money_transits") or []:
        if not isinstance(row, dict):
            continue
        if hit(row, ("from_account_id", "transit_account_id")):
            return True
        for close in row.get("closes") or []:
            if isinstance(close, dict) and str(close.get("to_account_id") or "") == aid:
                return True
    return False


def _finance_directory_forbidden(current: Any, incoming: Any, user: dict) -> bool:
    if not isinstance(incoming, dict):
        return True
    cur = current if isinstance(current, dict) else {}
    can_data = user_has_perm(user, "financeRec", "edit")
    can_setup = user_has_perm(user, "financeRecSetup", "edit")
    if not can_data:
        for key in FINANCE_DATA_KEYS:
            if key in incoming and not _same(incoming.get(key), cur.get(key)):
                return True
    if not can_setup:
        for key in ("sources", "areas", "categories", "removed_source_ids"):
            if key in incoming and not _same(incoming.get(key), cur.get(key)):
                return True
        return False
    areas = _user_finance_areas(user, cur)
    sources = _normalize_finance_sources(cur.get("sources"))
    by_id = {_source_id(row): row for row in sources if _source_id(row)}
    for row in sources:
        aid = str(row.get("account_id") or "").strip()
        if aid:
            by_id.setdefault(aid, row)
    # Если в PUT переданы sources, считаем удалением только id, которых там уже нет.
    # Иначе «залипшие» removed_source_ids блокируют создание новых касс/счетов.
    incoming_source_ids: set[str] | None = None
    if "sources" in incoming:
        incoming_source_ids = set()
        for row in _normalize_finance_sources(incoming.get("sources")):
            sid = _source_id(row)
            if sid:
                incoming_source_ids.add(sid)
            aid = str(row.get("account_id") or "").strip()
            if aid:
                incoming_source_ids.add(aid)
    for raw in incoming.get("removed_source_ids") or []:
        sid = str(raw or "").strip()
        src = by_id.get(sid)
        if src is None:
            for row in sources:
                if sid and sid in (_source_id(row), str(row.get("account_id") or "")):
                    src = row
                    break
        if src is None:
            continue
        src_sid = _source_id(src)
        src_aid = str(src.get("account_id") or src_sid)
        if incoming_source_ids is not None and (
            src_sid in incoming_source_ids or src_aid in incoming_source_ids
        ):
            continue
        area = _source_area(src)
        if area not in areas:
            return True
        if _finance_account_used(cur, area, src_aid or src_sid):
            return True
    return False


def _apply_finance_directory(base: dict, incoming: dict, user: dict) -> None:
    areas = _user_finance_areas(user, base)
    sources = _normalize_finance_sources(base.get("sources"))
    by_id: dict[str, dict] = {}
    for row in sources:
        sid = _source_id(row)
        if sid:
            by_id[sid] = row
            aid = str(row.get("account_id") or "")
            if aid:
                by_id.setdefault(aid, row)
    for raw_id in incoming.get("removed_source_ids") or []:
        sid = str(raw_id or "").strip()
        src = by_id.get(sid)
        if not isinstance(src, dict):
            continue
        area = _source_area(src)
        if area not in areas:
            continue
        if _finance_account_used(base, area, str(src.get("account_id") or _source_id(src))):
            continue
        sources = [row for row in sources if row is not src]
        by_id = {key: row for key, row in by_id.items() if row is not src}
    for item in incoming.get("sources") or []:
        if not isinstance(item, dict):
            continue
        area = _source_area(item)
        if area not in areas:
            continue
        sid = _source_id(item)
        dest = by_id.get(sid) if sid else None
        if dest is None and not sid:
            named = [
                row
                for row in sources
                if _source_area(row) == area
                and str(row.get("finance_source_id") or "").startswith("fsrc_legacy_")
                and str(row.get("account_id") or "") == str(row.get("finance_source_id") or "")
                and str(row.get("name") or "") == str(item.get("name") or "")
            ]
            if len(named) == 1:
                dest = named[0]
        if isinstance(dest, dict):
            if _source_area(dest) != area:
                continue
            for key in FINANCE_SOURCE_FIELDS:
                if key not in item:
                    continue
                if key == "account_type" and str(item.get(key) or "") not in FINANCE_ACCOUNT_TYPES:
                    continue
                dest[key] = _clone(item.get(key))
            continue
        if not sid:
            number = len(sources) + 1
            sid = "fsrc_%d" % number
            while sid in by_id:
                number += 1
                sid = "fsrc_%d" % number
        account_type = str(item.get("account_type") or "cash")
        if account_type not in FINANCE_ACCOUNT_TYPES:
            account_type = "cash"
        created = {key: _clone(item.get(key)) for key in FINANCE_SOURCE_FIELDS if key in item}
        created["account_type"] = account_type
        created["finance_source_id"] = sid
        created["account_id"] = str(item.get("account_id") or sid)
        created["object_id"] = area
        created["finance_area_id"] = area
        if "is_active" not in created:
            created["is_active"] = True
        if "in_actual_balance" not in created:
            created["in_actual_balance"] = True
        if "sort_order" not in created:
            created["sort_order"] = (len(sources) + 1) * 10
        sources.append(created)
        by_id[sid] = created
        by_id[created["account_id"]] = created
    base["sources"] = sources
    area_rows = base.get("areas") if isinstance(base.get("areas"), list) else []
    area_index = {
        str(row.get("object_id") or row.get("finance_area_id") or ""): row
        for row in area_rows
        if isinstance(row, dict)
    }
    for item in incoming.get("areas") or []:
        if not isinstance(item, dict):
            continue
        area = str(item.get("object_id") or item.get("finance_area_id") or "")
        dest = area_index.get(area)
        if area not in areas or not isinstance(dest, dict):
            continue
        if "primary_cash_source_id" not in item and "primary_cash_account_id" not in item:
            continue
        pid = str(item.get("primary_cash_source_id") or item.get("primary_cash_account_id") or "").strip()
        if not pid:
            dest["primary_cash_source_id"] = ""
            dest["primary_cash_account_id"] = ""
            continue
        match = None
        for row in sources:
            if _source_area(row) != area:
                continue
            if pid in (_source_id(row), str(row.get("account_id") or "")):
                match = row
                break
        if not match or match.get("account_type") != "cash" or match.get("is_active") is False:
            continue
        dest["primary_cash_source_id"] = _source_id(match)
        dest["primary_cash_account_id"] = str(match.get("account_id") or _source_id(match))
    categories = base.get("categories") if isinstance(base.get("categories"), list) else []
    categories = [row for row in categories if isinstance(row, dict)]
    cat_index = {
        str(row.get("finance_category_id")): row
        for row in categories
        if row.get("finance_category_id") not in (None, "")
    }
    for item in incoming.get("categories") or []:
        if not isinstance(item, dict):
            continue
        area = _source_area(item)
        if area and area not in areas:
            continue
        cid = str(item.get("finance_category_id") or "").strip()
        if cid and cid in cat_index:
            dest = cat_index[cid]
            for key in ("name", "sort_order", "is_active", "default_type"):
                if key in item:
                    dest[key] = _clone(item.get(key))
            continue
        if not cid:
            cid = "fcat_%d" % (len(categories) + 1)
        created = {
            "finance_category_id": cid,
            "name": item.get("name") or "",
            "sort_order": item.get("sort_order") if item.get("sort_order") is not None else (len(categories) + 1) * 10,
            "is_active": item.get("is_active") is not False,
            "default_type": item.get("default_type") or "any",
            "object_id": area,
            "finance_area_id": area,
        }
        categories.append(created)
        cat_index[cid] = created
    base["categories"] = categories
    base["removed_source_ids"] = []


def _apply_finance_rec(current: Any, incoming: Any, user: dict) -> Any:
    base = _clone(current) if isinstance(current, dict) else {}
    if not isinstance(incoming, dict):
        return base
    if user_has_perm(user, "financeRec", "edit"):
        for key in FINANCE_DATA_KEYS:
            if key in incoming:
                base[key] = _overlay_open(base.get(key), incoming.get(key), True)
    if user_has_perm(user, "financeRecSetup", "edit"):
        _apply_finance_directory(base, incoming, user)
    return base


def _can_write_top(user: dict, key: str) -> bool:
    codes = user_dept_codes(user)
    if key == "shopMetrics":
        return "shop" in codes and (_can_edit_metric_input(user) or _can_edit_metric_setup(user))
    if key == "shopRevision":
        return "shop" in codes and user_has_perm(user, "revision", "edit")
    if key == "shopSalesPlan":
        return "shop" in codes and user_has_perm(user, "salesPlan", "edit")
    if key == "shopMetricCatalog":
        return _can_edit_metric_setup(user)
    if key == "shopPayroll":
        return "shop" in codes and user_has_perm(user, "salary", "edit")
    if key == "salary":
        return user_has_perm(user, "salary", "edit")
    if key == "financeRec":
        return user_has_perm(user, "financeRec", "edit") or user_has_perm(user, "financeRecSetup", "edit")
    if key == "management":
        return _can_write_any_management(user)
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
        return _can_edit_metric_input(user) or _can_edit_metric_setup(user)
    if key == "deptRevisions":
        return user_has_perm(user, "revision", "edit")
    if key == "deptSalesPlans":
        return user_has_perm(user, "salesPlan", "edit")
    return False


def _is_cleared_timesheet_cell(rec: Any) -> bool:
    if rec is None or rec == "":
        return True
    if isinstance(rec, dict):
        status = str(rec.get("status") or "").strip()
        hours = rec.get("hours")
        late = rec.get("lateMinutes") or rec.get("late") or 0
        empty_hours = hours in (None, "", 0, "0")
        try:
            empty_late = float(late or 0) == 0
        except (TypeError, ValueError):
            empty_late = not late
        return not status and empty_hours and empty_late
    return False


def _overlay_timesheet(current: Any, incoming: Any) -> Any:
    """День из запроса заменяет ячейку целиком, пустая ячейка не поднимает старое значение."""
    if not isinstance(incoming, dict):
        return _clone(incoming)
    out = _clone(current) if isinstance(current, dict) else {}
    for month, month_val in incoming.items():
        month_key = str(month)
        if month_val is None:
            out.pop(month_key, None)
            continue
        if not isinstance(month_val, dict):
            out[month_key] = _clone(month_val)
            continue
        base_month = out.get(month_key) if isinstance(out.get(month_key), dict) else {}
        next_month = _clone(base_month)
        for emp_id, days in month_val.items():
            emp_key = str(emp_id)
            if days is None:
                next_month.pop(emp_key, None)
                continue
            if not isinstance(days, dict):
                next_month[emp_key] = _clone(days)
                continue
            base_days = next_month.get(emp_key) if isinstance(next_month.get(emp_key), dict) else {}
            next_days = _clone(base_days)
            for day, rec in days.items():
                day_key = str(day)
                if rec is None or _is_cleared_timesheet_cell(rec):
                    next_days[day_key] = {"status": "", "hours": "", "lateMinutes": 0}
                elif isinstance(rec, dict):
                    next_days[day_key] = _clone(rec)
                else:
                    next_days[day_key] = rec
            next_month[emp_key] = next_days
        out[month_key] = next_month
    return out


def _closed_is_blank(val: Any) -> bool:
    if val is None or val == "" or val == {} or val == []:
        return True
    if isinstance(val, bool):
        return False
    if isinstance(val, (int, float)) and val == 0:
        return True
    return False


def _dept_employees_forbidden(current: Any, incoming: Any, user: dict) -> bool:
    if not isinstance(incoming, list):
        return True
    current_list = current if isinstance(current, list) else []
    by_id: dict[str, dict] = {}
    for row in current_list:
        if isinstance(row, dict) and row.get("id") not in (None, ""):
            by_id[str(row.get("id"))] = row
    allow_salary = user_has_perm(user, "salary", "edit")
    for item in incoming:
        if not isinstance(item, dict):
            return True
        emp_id = str(item.get("id") or "").strip()
        if not emp_id:
            return True
        existing = by_id.get(emp_id)
        if existing is None:
            for key, val in item.items():
                if _is_closed_key(str(key), allow_salary) and not _closed_is_blank(val):
                    return True
            continue
        stored_eid = str(existing.get("employee_id") or "").strip()
        if stored_eid and "employee_id" in item and str(item.get("employee_id") or "").strip() != stored_eid:
            return True
        if _closed_field_violations(existing, item, allow_salary):
            return True
    return False


def _merge_dept_employees(current: Any, incoming: Any, allow_salary: bool) -> list:
    """Добавляет и обновляет карточки по id. Отсутствующие в запросе не удаляет."""
    current_list = _clone(current) if isinstance(current, list) else []
    index: dict[str, int] = {}
    for i, row in enumerate(current_list):
        if isinstance(row, dict) and row.get("id") not in (None, ""):
            index[str(row.get("id"))] = i
    for item in incoming:
        if not isinstance(item, dict):
            continue
        emp_id = str(item.get("id") or "").strip()
        if not emp_id:
            continue
        if emp_id in index:
            dest = current_list[index[emp_id]]
            if not isinstance(dest, dict):
                continue
            patch = _clone(item)
            patch["id"] = dest.get("id")
            if str(dest.get("employee_id") or "").strip():
                patch["employee_id"] = dest.get("employee_id")
            current_list[index[emp_id]] = _overlay_open(dest, patch, allow_salary)
            continue
        patch = _overlay_open({}, item, allow_salary)
        if not isinstance(patch, dict):
            patch = {}
        patch["id"] = emp_id
        if not str(patch.get("employee_id") or "").strip():
            patch["employee_id"] = emp_id
        current_list.append(patch)
        index[emp_id] = len(current_list) - 1
    return current_list


def _merge_dept_employee_ids(current_ids: Any, incoming_ids: Any, employees: Any) -> list:
    known: set[str] = set()
    if isinstance(employees, list):
        for row in employees:
            if isinstance(row, dict) and row.get("id") not in (None, ""):
                known.add(str(row.get("id")))
    out: list[str] = []
    seen: set[str] = set()

    def add(raw: Any) -> None:
        if isinstance(raw, bool) or raw is None:
            return
        key = str(raw).strip()
        if not key or key not in known or key in seen:
            return
        seen.add(key)
        out.append(key)

    if isinstance(current_ids, list):
        for raw in current_ids:
            add(raw)
    if isinstance(incoming_ids, list):
        for raw in incoming_ids:
            add(raw)
    return out


def _empty_wipe(val: Any, existing: Any) -> bool:
    if val is None:
        return True
    if isinstance(val, (dict, list)) and not val and existing:
        return True
    return False


def _normalize_employee_removals(raw: Any) -> list[dict[str, str]]:
    """Явные удаления: [{department_id, employee_id}, ...]. Без этого карточки не удаляются."""
    out: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        dept = str(
            item.get("department_id") or item.get("dept") or item.get("code") or ""
        ).strip()
        eid = str(item.get("employee_id") or item.get("id") or "").strip()
        if not dept or not eid or dept == "summary":
            continue
        key = (dept, eid)
        if key in seen:
            continue
        seen.add(key)
        out.append({"department_id": dept, "employee_id": eid})
    return out


def apply_employee_removals(payload: dict, removals: Any, allowed_depts: set[str] | None = None) -> None:
    """Удаляет карточки только по явному списку. Не трогает чужие отделы вне allowed_depts."""
    if not isinstance(payload, dict):
        return
    for item in _normalize_employee_removals(removals):
        dept = item["department_id"]
        eid = item["employee_id"]
        if allowed_depts is not None and dept not in allowed_depts:
            continue
        ws = _find_workspace(payload.get("workspaces"), dept)
        if not isinstance(ws, dict):
            continue
        emps = ws.get("employees")
        if isinstance(emps, list):
            ws["employees"] = [
                row
                for row in emps
                if not (isinstance(row, dict) and str(row.get("id") or "").strip() == eid)
            ]
        ids = ws.get("employeeIds")
        if isinstance(ids, list):
            ws["employeeIds"] = [x for x in ids if str(x).strip() != eid]


def _trim_employee_create_idempotency(payload: dict, keep: int = 300) -> None:
    raw = payload.get("employeeCreateIdempotency")
    if not isinstance(raw, dict) or len(raw) <= keep:
        return
    # dict сохраняет порядок вставки — выкидываем самые старые.
    overflow = len(raw) - keep
    for key in list(raw.keys())[:overflow]:
        raw.pop(key, None)


def find_employee_in_payload(payload: dict, dept: str, employee_id: str) -> dict | None:
    ws = _find_workspace((payload or {}).get("workspaces"), dept)
    if not isinstance(ws, dict):
        return None
    eid = str(employee_id or "").strip()
    for row in ws.get("employees") or []:
        if isinstance(row, dict) and str(row.get("id") or "").strip() == eid:
            return row
    return None


def build_created_employee_record(employee_id: str, data: dict, dept_code: str, dept_name: str) -> dict:
    """Карточка нового сотрудника для атомарного POST /api/employees."""
    eid = str(employee_id).strip()
    name = str(data.get("name") or "").strip()
    position = str(data.get("position") or "").strip()
    position_id = str(data.get("position_id") or "").strip()
    work_tab_id = str(data.get("work_tab_id") or data.get("work_group_id") or "").strip()
    job_status = "fired" if str(data.get("jobStatus") or "") == "fired" else "working"
    meet_on = data.get("participates_in_spu_meetings") is True or data.get("meeting_participant") is True
    rec = {
        "id": eid,
        "employee_id": eid,
        "external_1c_id": "",
        "name": name,
        "last_name": str(data.get("last_name") or "").strip(),
        "first_name": str(data.get("first_name") or "").strip(),
        "middle_name": str(data.get("middle_name") or "").strip(),
        "position": position,
        "position_id": position_id,
        "work_tab_id": work_tab_id,
        "work_tab_from": str(data.get("work_tab_from") or "").strip(),
        "work_tab_history": [],
        "department": dept_name or str(data.get("department") or "").strip(),
        "start": str(data.get("start") or "08:00"),
        "end": str(data.get("end") or "17:00"),
        "rate": 0,
        "taxiDaily": 0,
        "jobStatus": job_status,
        "participates_in_spu_meetings": meet_on,
        "meeting_participant": meet_on,
        "terms": {},
    }
    return rec


def _merge_admin_payload(current: dict, incoming: dict) -> dict:
    stored = _clone(current)
    for key, val in incoming.items():
        if key in ("employeeRemovals", "employeeCreateIdempotency"):
            # Removals применяются отдельно; idempotency — только серверный словарь.
            continue
        if key == "employeeIdCounters":
            stored["employeeIdCounters"] = merge_employee_id_counters(
                stored.get("employeeIdCounters"), val
            )
            continue
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
                        if ws_key in ("removedEmployeeIds", "employeeRemovals"):
                            continue
                        if _empty_wipe(ws_val, dest.get(ws_key)):
                            continue
                        # employees: upsert по id — не затираем карточки другого админа.
                        if ws_key == "employees" and isinstance(ws_val, list):
                            dest["employees"] = _merge_dept_employees(
                                dest.get("employees"), ws_val, True
                            )
                            continue
                        if ws_key == "employeeIds" and isinstance(ws_val, list):
                            dest["employeeIds"] = _merge_dept_employee_ids(
                                dest.get("employeeIds"), ws_val, dest.get("employees")
                            )
                            continue
                        dest[ws_key] = _clone(ws_val)
                    # Явные удаления внутри workspace (дополнительно к employeeRemovals).
                    local_rm = item.get("removedEmployeeIds")
                    if isinstance(local_rm, list) and local_rm:
                        apply_employee_removals(
                            {"workspaces": [dest]},
                            [
                                {"department_id": code, "employee_id": x}
                                for x in local_rm
                            ],
                        )
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
        elif key in ("shopMetrics", "deptMetrics"):
            # Не затираем целиком: иначе устаревший снимок админа стирает свежий ввод сотрудника.
            # workAccess/extraAccess мержим отдельно — иначе overlay по спискам назначений мог
            # оставлять галочки в несогласованном состоянии относительно карточки сотрудника.
            if _empty_wipe(val, stored.get(key)):
                continue
            if key == "deptMetrics" and isinstance(val, dict):
                base_map = stored.get(key) if isinstance(stored.get(key), dict) else {}
                next_map = _clone(base_map)
                for dept, branch in val.items():
                    next_map[str(dept)] = _merge_metric_branch_admin(base_map.get(str(dept)), branch)
                stored[key] = next_map
            else:
                stored[key] = _merge_metric_branch_admin(stored.get(key), val)
        elif _empty_wipe(val, stored.get(key)):
            continue
        else:
            stored[key] = _clone(val)
    # Удаление только по явному employeeRemovals — иначе чужие новые карточки не затираются.
    apply_employee_removals(stored, incoming.get("employeeRemovals"), None)
    stored.pop("employeeRemovals", None)
    return hash_pins_in_payload(stored, current)


def _staff_put_forbidden(current: dict, incoming: dict, user: dict) -> bool:
    codes = user_dept_codes(user)
    for key, val in incoming.items():
        if key == "employeeRemovals":
            if not can_write_dept_employees(user):
                return True
            for item in _normalize_employee_removals(val):
                if item["department_id"] not in codes:
                    return True
            continue
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
                    if field == "employees":
                        if not can_write_dept_employees(user):
                            if not _same(field_val, cur_ws.get(field)):
                                return True
                            continue
                        if _dept_employees_forbidden(cur_ws.get("employees"), field_val, user):
                            return True
                        continue
                    if field == "employeeIds":
                        if not can_write_dept_employees(user):
                            if not _same(field_val, cur_ws.get(field)):
                                return True
                            continue
                        if not isinstance(field_val, list) or any(
                            isinstance(item, bool) or not isinstance(item, (str, int))
                            for item in field_val
                        ):
                            return True
                        continue
                    if field == "removedEmployeeIds":
                        if not can_write_dept_employees(user):
                            return True
                        if field_val is not None and not isinstance(field_val, list):
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
                if key == "deptMetrics":
                    if _metric_root_forbidden(current_map.get(dept_code), branch, user):
                        return True
                    continue
                if _closed_field_violations(current_map.get(dept_code), branch, allow_sal):
                    return True
            continue
        if key == "management" and _can_write_any_management(user):
            if not isinstance(val, dict):
                return True
            cur_m = current.get("management") if isinstance(current.get("management"), dict) else {}
            allow_sal = user_has_perm(user, "salary", "edit")
            for mkey, mval in val.items():
                if _can_write_mgmt_key(user, str(mkey)):
                    if _closed_field_violations(cur_m.get(mkey), mval, allow_sal):
                        return True
                    continue
                if not _same(mval, cur_m.get(mkey)):
                    return True
            continue
        if key == "shopMetrics":
            if "shop" not in user_dept_codes(user) or not (
                _can_edit_metric_input(user) or _can_edit_metric_setup(user)
            ):
                if not _same(val, current.get(key)):
                    return True
                continue
            if _metric_root_forbidden(current.get(key), val, user):
                return True
            continue
        if key == "shopMetricCatalog":
            if _catalog_forbidden(current.get(key) if isinstance(current.get(key), dict) else {}, val, user):
                return True
            continue
        if key == "shopPayroll":
            if not _can_write_top(user, key):
                if not _same(val, current.get(key)):
                    return True
                continue
            if _shop_payroll_kpi_money_forbidden(current.get(key), val, user):
                return True
            allow_sal = user_has_perm(user, "salary", "edit")
            if _closed_field_violations(current.get(key), val, allow_sal):
                return True
            continue
        if key == "financeRec":
            if not (
                user_has_perm(user, "financeRec", "edit") or user_has_perm(user, "financeRecSetup", "edit")
            ):
                if not _same(val, current.get(key)):
                    return True
                continue
            if _finance_directory_forbidden(current.get(key), val, user):
                return True
            if user_has_perm(user, "financeRec", "edit") and isinstance(val, dict):
                cur_fin = current.get(key) if isinstance(current.get(key), dict) else {}
                for data_key in FINANCE_DATA_KEYS:
                    if data_key in val and _closed_field_violations(cur_fin.get(data_key), val.get(data_key), True):
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
    if isinstance(incoming.get("workspaces"), list):
        codes = user_dept_codes(user)
        for in_ws in incoming.get("workspaces") or []:
            if not isinstance(in_ws, dict):
                continue
            code = str(in_ws.get("code") or "")
            if code not in codes:
                continue
            cur_ws = _find_workspace(stored.get("workspaces"), code)
            if cur_ws is None:
                continue
            if "timesheet" in in_ws and user_has_perm(user, "timesheet", "edit"):
                cur_ws["timesheet"] = _overlay_timesheet(cur_ws.get("timesheet"), in_ws.get("timesheet"))
            if can_write_dept_employees(user):
                allow_sal = user_has_perm(user, "salary", "edit")
                if isinstance(in_ws.get("employees"), list):
                    cur_ws["employees"] = _merge_dept_employees(
                        cur_ws.get("employees"), in_ws.get("employees"), allow_sal
                    )
                if isinstance(in_ws.get("employeeIds"), list):
                    cur_ws["employeeIds"] = _merge_dept_employee_ids(
                        cur_ws.get("employeeIds"), in_ws.get("employeeIds"), cur_ws.get("employees")
                    )
                local_rm = in_ws.get("removedEmployeeIds")
                if isinstance(local_rm, list) and local_rm:
                    apply_employee_removals(
                        stored,
                        [{"department_id": code, "employee_id": x} for x in local_rm],
                        set(codes),
                    )
    if can_write_dept_employees(user) and "employeeRemovals" in incoming:
        apply_employee_removals(stored, incoming.get("employeeRemovals"), set(user_dept_codes(user)))
    if "management" in incoming and _can_write_any_management(user):
        incoming_m = incoming.get("management")
        cur_m = stored.get("management") if isinstance(stored.get("management"), dict) else {}
        allow_sal = user_has_perm(user, "salary", "edit")
        if user_has_perm(user, "management", "edit") and isinstance(incoming_m, dict):
            stored["management"] = _overlay_open(cur_m, incoming_m, allow_sal)
        elif isinstance(incoming_m, dict):
            next_m = _clone(cur_m)
            for mkey, mval in incoming_m.items():
                if _can_write_mgmt_key(user, str(mkey)):
                    next_m[mkey] = _overlay_open(cur_m.get(mkey), mval, allow_sal)
            stored["management"] = next_m
    emp_dept = _employee_dept_map(stored)
    for key in (
        "shopMetrics",
        "shopRevision",
        "shopSalesPlan",
        "shopMetricCatalog",
        "shopPayroll",
        "salary",
        "financeRec",
        "orgStructure",
        "customerReturns",
    ):
        if key not in incoming or not _can_write_top(user, key):
            continue
        if key == "shopMetrics":
            stored[key] = _apply_metric_root(stored.get(key), incoming.get(key), user, "shop", emp_dept)
            continue
        if key == "shopMetricCatalog":
            stored[key] = _apply_metric_catalog(stored.get(key), incoming.get(key), user, emp_dept)
            continue
        if key == "shopPayroll":
            allow_sal = user_has_perm(user, "salary", "edit")
            cur_pay = stored.get(key) if isinstance(stored.get(key), dict) else {}
            merged = _overlay_open(cur_pay, incoming.get(key), allow_sal)
            if isinstance(merged, dict) and not is_app_admin(user):
                for money_key in SHOP_PAYROLL_KPI_MONEY_KEYS:
                    if money_key in cur_pay:
                        merged[money_key] = _clone(cur_pay.get(money_key))
                    else:
                        merged.pop(money_key, None)
            stored[key] = merged
            continue
        if key == "financeRec":
            stored[key] = _apply_finance_rec(stored.get(key), incoming.get(key), user)
            continue
        allow_sal = user_has_perm(user, "salary", "edit") or key == "financeRec"
        stored[key] = _overlay_open(stored.get(key), incoming.get(key), allow_sal)
    for key in ("deptMetrics", "deptRevisions", "deptSalesPlans"):
        if key not in incoming or not isinstance(incoming.get(key), dict):
            continue
        if not isinstance(stored.get(key), dict):
            stored[key] = {}
        allow_sal = user_has_perm(user, "salary", "edit")
        for dept, branch in incoming[key].items():
            dept_code = str(dept)
            if not _can_write_dept_map(user, key, dept_code):
                continue
            if key == "deptMetrics":
                stored[key][dept_code] = _apply_metric_root(
                    stored[key].get(dept_code), branch, user, dept_code, emp_dept
                )
                continue
            stored[key][dept_code] = _overlay_open(stored[key].get(dept_code), branch, allow_sal)
    return stored


def incoming_admin_pin_duplicate(current: dict, incoming: dict) -> bool:
    incoming_users = incoming.get("users")
    if not isinstance(incoming_users, list):
        return False
    current_users = current.get("users") if isinstance(current.get("users"), list) else []
    current_by_id = {
        str(item.get("user_id")): item
        for item in current_users
        if isinstance(item, dict) and item.get("user_id")
    }
    incoming_by_id = {
        str(item.get("user_id")): item
        for item in incoming_users
        if isinstance(item, dict) and item.get("user_id")
    }

    def admin_record(uid: str) -> dict | None:
        inc = incoming_by_id.get(uid)
        cur = current_by_id.get(uid)
        rec = inc if inc is not None else cur
        if not rec or rec.get("is_active") is False:
            return None
        if is_app_admin(rec):
            return rec
        return None

    admin_ids = [uid for uid in set(current_by_id) | set(incoming_by_id) if admin_record(uid)]
    raw_pins: dict[str, str] = {}
    for uid in admin_ids:
        inc = incoming_by_id.get(uid)
        if not inc:
            continue
        raw = str(inc.get("pin") or inc.get("password") or "").strip()
        if raw:
            raw_pins[uid] = raw
    seen_raw: dict[str, str] = {}
    for uid, raw in raw_pins.items():
        if raw in seen_raw:
            return True
        seen_raw[raw] = uid
        for other_uid in admin_ids:
            if other_uid == uid:
                continue
            other_inc = incoming_by_id.get(other_uid)
            other_cur = current_by_id.get(other_uid)
            other_raw = ""
            if other_inc:
                other_raw = str(other_inc.get("pin") or other_inc.get("password") or "").strip()
            if other_raw and pin_secrets_equal(raw, other_raw):
                return True
            other_hash = ""
            if other_inc:
                other_hash = str(other_inc.get("pin_hash") or "")
            if not other_hash and other_cur:
                other_hash = str(other_cur.get("pin_hash") or "")
            if other_hash and verify_pin(raw, other_hash):
                return True
            other_plain = ""
            if other_cur:
                other_plain = str(other_cur.get("pin") or other_cur.get("password") or "").strip()
            if other_plain and pin_secrets_equal(raw, other_plain):
                return True
    return False


def prepare_put_payload(current: dict, incoming: dict, user: dict) -> tuple[dict | None, str | None]:
    """Пишем поверх снимка в БД. Запрещённое поле — 403, без частичной записи."""
    if not isinstance(current, dict):
        current = {}
    if not isinstance(incoming, dict):
        return None, "forbidden"
    # Новые дубли ID блокируем; исторические (уже в current) — нет.
    if employee_id_put_denied(current, incoming):
        return None, "duplicate_employee_id"
    if is_app_admin(user):
        if incoming_admin_pin_duplicate(current, incoming):
            return None, "duplicate_pin"
        return _merge_admin_payload(current, incoming), None
    if _staff_put_forbidden(current, incoming, user):
        return None, "forbidden"
    stored = _apply_staff_writes(current, incoming, user)
    # Staff PUT обычно не шлёт counters — сохраняем серверные после reserve.
    if isinstance(stored, dict) and "employeeIdCounters" not in incoming:
        if isinstance(current.get("employeeIdCounters"), dict):
            stored["employeeIdCounters"] = _clone(current.get("employeeIdCounters"))
    return stored, None


def payload_for_client(user: dict | None, payload: dict) -> dict:
    safe = strip_secrets(payload)
    if is_app_admin(user):
        return safe
    out = dict(safe) if isinstance(safe, dict) else {}
    out.pop("users", None)
    if not user_has_perm(user, "salary", "view"):
        out.pop("salary", None)
    if not (
        user_has_perm(user, "financeRec", "view")
        or user_has_perm(user, "financeRecSetup", "view")
    ):
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
    allow_finance = user_has_perm(user, "financeRec", "view") or user_has_perm(user, "financeRecSetup", "view")
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
    # Справочник метрик общий: нужен складу/территории без галочки shop.
    if can_metrics and "shopMetricCatalog" in payload:
        out["shopMetricCatalog"] = drop_closed_fields(
            payload.get("shopMetricCatalog"), allow_salary, allow_finance
        )

    if "shop" in codes:
        if can_metrics and "shopMetrics" in payload:
            out["shopMetrics"] = drop_closed_fields(payload.get("shopMetrics"), allow_salary, allow_finance)
        if user_has_perm(user, "revision", "view") and "shopRevision" in payload:
            out["shopRevision"] = drop_closed_fields(payload.get("shopRevision"), allow_salary, allow_finance)
        if user_has_perm(user, "salesPlan", "view") and "shopSalesPlan" in payload:
            out["shopSalesPlan"] = drop_closed_fields(payload.get("shopSalesPlan"), allow_salary, allow_finance)
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

    if allow_salary and "salary" in payload:
        out["salary"] = drop_closed_fields(payload.get("salary"), True, allow_finance)
    if allow_finance and "financeRec" in payload:
        out["financeRec"] = drop_closed_fields(payload.get("financeRec"), allow_salary, True)
    if user_can_open_management(user) and "management" in payload:
        out["management"] = drop_closed_fields(payload.get("management"), allow_salary, allow_finance)
    if user_has_perm(user, "orgStructure", "view") and "orgStructure" in payload:
        out["orgStructure"] = drop_closed_fields(payload.get("orgStructure"), allow_salary, allow_finance)
    if user_has_perm(user, "customerReturns", "view") and "customerReturns" in payload:
        out["customerReturns"] = drop_closed_fields(payload.get("customerReturns"), allow_salary, allow_finance)
    return out


def user_login_codes(user: dict) -> list[str]:
    codes: list[str] = []
    depts = user_dept_codes(user)
    admin = is_app_admin(user)
    for code in ("shop", "office", "warehouse", "drivers", "summary"):
        if admin or code in depts:
            codes.append(code)
    if admin or "org" in depts or user_has_perm(user, "orgStructure", "view"):
        codes.append("org")
    if admin or "spu" in depts or user_can_open_management(user):
        codes.append("spu")
    if (
        admin
        or "financeRec" in depts
        or user_has_perm(user, "financeRec", "view")
        or user_has_perm(user, "financeRecSetup", "view")
    ):
        codes.append("financeRec")
    if admin or bool(depts & TERRITORY_CODES):
        codes.append("territoryMetrics")
    if admin:
        codes.append("admin")
    return codes


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
                "login_codes": user_login_codes(user),
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
            user_id = str(user_id or "")
            if user_id.startswith(LOGIN_FAIL_USER_PREFIX):
                conn.commit()
                return ""
            exp = _as_utc(expires_at)
            if not exp or exp <= datetime.now(timezone.utc):
                cur.execute(
                    "DELETE FROM app_sessions WHERE token_hash = %s",
                    (token_hash(raw),),
                )
                conn.commit()
                return ""
            conn.commit()
            return user_id
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


def login_fail_marker(key: str) -> str:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return LOGIN_FAIL_USER_PREFIX + digest


def login_blocked(key: str) -> bool:
    now = time.time()
    hits = [t for t in LOGIN_FAILS.get(key, []) if now - t < LOGIN_WINDOW_SEC]
    LOGIN_FAILS[key] = hits
    if len(hits) >= LOGIN_MAX_FAILS:
        return True
    if not db_configured():
        return False
    marker = login_fail_marker(key)
    now_dt = datetime.now(timezone.utc)
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM app_sessions WHERE user_id = %s AND expires_at < %s",
                (marker, now_dt),
            )
            cur.execute(
                "SELECT COUNT(*) FROM app_sessions WHERE user_id = %s AND expires_at > %s",
                (marker, now_dt),
            )
            row = cur.fetchone()
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()
    count = int(row[0]) if row else 0
    return count >= LOGIN_MAX_FAILS


def register_login_fail(key: str) -> None:
    LOGIN_FAILS.setdefault(key, []).append(time.time())
    if not db_configured():
        return
    marker = login_fail_marker(key)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=LOGIN_WINDOW_SEC)
    conn = connect()
    try:
        with conn.cursor() as cur:
            insert_session(cur, token_hash(secrets.token_urlsafe(16)), marker, expires_at)
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


def clear_login_fails(key: str) -> None:
    LOGIN_FAILS.pop(key, None)
    if not db_configured():
        return
    marker = login_fail_marker(key)
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM app_sessions WHERE user_id = %s", (marker,))
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


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
    """Пишет снимок. RETURNING без payload — иначе каждый PUT гоняет весь JSON обратно из PG."""
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
        RETURNING version, updated_at, updated_by
        """,
        (json.dumps(payload, ensure_ascii=False), updated_by, STATE_ID, expected_version),
    )
    return cur.fetchone()


def fetch_state_for_update(cur) -> dict:
    """Блокирует строку app_state для атомарного резерва ID."""
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
        SELECT payload, version, updated_at, updated_by
        FROM app_state
        WHERE id = %s
        FOR UPDATE
        """,
        (STATE_ID,),
    )
    row = cur.fetchone()
    if not row:
        return {"version": 0, "payload": {}, "updated_at": "", "updated_by": ""}
    return row_to_state(row)


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


def invalid_login():
    return json_bytes({"error": "invalid login"}, "401 Unauthorized")


def login_too_many():
    return json_bytes({"error": "too many attempts"}, "429 Too Many Requests")


def issue_login_session(user: dict, key: str):
    try:
        clear_login_fails(key)
    except Exception:
        return db_unavailable()
    user_id = str(user.get("user_id") or "")
    try:
        token, ttl = create_session(user_id)
    except Exception:
        return db_unavailable()
    extra = [("Set-Cookie", cookie_header(token, ttl))]
    return json_bytes({"user": public_user(user)}, extra=extra)


def active_admin_users(payload: dict) -> list[dict]:
    users = payload.get("users") if isinstance(payload.get("users"), list) else []
    out = []
    for item in users:
        if isinstance(item, dict) and is_app_admin(item):
            out.append(item)
    return out


def match_admins_by_pin(payload: dict, pin: str) -> list[dict]:
    hits = []
    for user in active_admin_users(payload):
        ok, _ = pins_match_user(user, pin)
        if ok:
            hits.append(user)
    return hits


def try_first_admin_bootstrap(payload: dict, state: dict, pin: str, key: str):
    bootstrap_pin = _env("WT_BOOTSTRAP_PIN").strip()
    display_name = _env("WT_BOOTSTRAP_NAME").strip()
    user_id = _env("WT_BOOTSTRAP_USER_ID").strip() or "user_admin"
    dummy = hash_pin(pin)
    allowed = bool(bootstrap_pin) and bool(display_name) and BOOTSTRAP_USER_ID_RE.match(user_id)
    matched = bool(allowed) and pin_secrets_equal(pin, bootstrap_pin)
    _ = dummy
    if not matched:
        try:
            register_login_fail(key)
        except Exception:
            return db_unavailable()
        return invalid_login()
    try:
        user = bootstrap_admin_record(display_name, pin, user_id)
        merged, reason = merge_bootstrap_user(payload, user)
        if reason or merged is None:
            register_login_fail(key)
            return invalid_login()
        conn = connect()
        try:
            with conn.cursor() as cur:
                row = persist_bootstrap_admin(
                    cur, merged, int(state.get("version") or 0), user_id
                )
            if not row:
                conn.rollback()
                register_login_fail(key)
                return invalid_login()
            conn.commit()
        finally:
            conn.close()
    except PinPepperMissing:
        return auth_unavailable()
    except Exception:
        try:
            register_login_fail(key)
        except Exception:
            return db_unavailable()
        return invalid_login()
    return issue_login_session(user, key)


def handle_admin_pin_login(pin: str, payload: dict, state: dict, key: str):
    dummy = hash_pin(pin)
    if not payload_has_users(payload):
        _ = dummy
        return try_first_admin_bootstrap(payload, state, pin, key)
    hits = match_admins_by_pin(payload, pin)
    _ = dummy
    if len(hits) != 1:
        try:
            register_login_fail(key)
        except Exception:
            return db_unavailable()
        return invalid_login()
    user = hits[0]
    ok, needs_hash = pins_match_user(user, pin)
    if not ok:
        try:
            register_login_fail(key)
        except Exception:
            return db_unavailable()
        return invalid_login()
    if needs_hash:
        user["pin_hash"] = hash_pin(pin)
        user.pop("pin", None)
        user.pop("password", None)
        try:
            conn = connect()
            try:
                with conn.cursor() as cur:
                    persist_payload(cur, payload, int(state.get("version") or 0), str(user.get("user_id") or ""))
                conn.commit()
            finally:
                conn.close()
        except Exception:
            pass
    return issue_login_session(user, key)


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
    if not pin:
        return json_bytes({"error": "invalid login"}, "400 Bad Request")
    if not pin_pepper_ready():
        return auth_unavailable()
    key = client_key(environ, user_id or "admin")
    try:
        if login_blocked(key):
            return login_too_many()
    except Exception:
        return db_unavailable()
    state, err = load_state_or_error()
    if err:
        return err
    payload = state.get("payload") if state else None
    if not isinstance(payload, dict):
        payload = {}
    if not user_id:
        return handle_admin_pin_login(pin, payload, state, key)
    user = find_user(payload, user_id)
    ok, needs_hash = pins_match_user(user or {}, pin) if user else (False, False)
    dummy = hash_pin(pin)
    if not user or user.get("is_active") is False or not ok:
        try:
            register_login_fail(key)
        except Exception:
            return db_unavailable()
        _ = dummy
        return invalid_login()
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
    return issue_login_session(user, key)


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
    if denied == "duplicate_pin":
        return json_bytes({"error": "duplicate pin"}, "400 Bad Request")
    if denied == "duplicate_employee_id":
        return json_bytes(
            {"error": "duplicate employee id", "message": "Этот ID сотрудника уже используется."},
            "400 Bad Request",
        )
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
                version, updated_at, updated_by_row = row[0], row[1], row[2]
                # Клиент после PUT читает только version — полный payload не отдаём (экономия сети/CPU).
                return json_bytes({
                    "ok": True,
                    "version": int(version or 0),
                    "updated_at": updated_at.isoformat() if hasattr(updated_at, "isoformat") else (updated_at or ""),
                    "updated_by": updated_by_row or "",
                })
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


def handle_reserve_employee_id(environ):
    """Атомарная выдача уникального ID нового сотрудника (без миграции схемы)."""
    raw = read_body(environ)
    try:
        data = json.loads(raw.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return json_bytes({"error": "invalid json"}, "400 Bad Request")
    if not isinstance(data, dict):
        return json_bytes({"error": "body must be an object"}, "400 Bad Request")
    dept = str(data.get("department_id") or data.get("dept") or data.get("code") or "").strip()
    if not employee_id_prefix_for_dept(dept):
        return json_bytes({"error": "invalid department"}, "400 Bad Request")
    user_id, err = require_session(environ)
    if err:
        return err
    if not db_configured():
        return db_unavailable()
    try:
        conn = connect()
    except Exception:
        return db_unavailable()
    try:
        with conn.cursor() as cur:
            state = fetch_state_for_update(cur)
            payload = state.get("payload") if isinstance(state.get("payload"), dict) else {}
            payload = _clone(payload) if payload else {}
            user = current_user_from_state(environ, payload, user_id)
            if not user:
                conn.rollback()
                return json_bytes({"error": "unauthorized"}, "401 Unauthorized")
            if not is_app_admin(user):
                if not can_write_dept_employees(user):
                    conn.rollback()
                    return json_bytes({"error": "forbidden"}, "403 Forbidden")
                if dept not in user_dept_codes(user):
                    conn.rollback()
                    return json_bytes({"error": "forbidden"}, "403 Forbidden")
            eid, alloc_err = allocate_employee_id_in_payload(payload, dept)
            if alloc_err or not eid:
                conn.rollback()
                return json_bytes({"error": alloc_err or "allocate failed"}, "400 Bad Request")
            try:
                expected = int(state.get("version") or 0)
            except (TypeError, ValueError):
                expected = 0
            updated_by = str(user.get("user_id") or "")[:120]
            row = persist_payload(cur, payload, expected, updated_by)
            if not row:
                conn.rollback()
                return json_bytes({"error": "conflict", "message": "Повторите создание сотрудника."}, "409 Conflict")
            conn.commit()
            version = int(row[0] or 0)
            return json_bytes({
                "ok": True,
                "employee_id": eid,
                "department_id": dept,
                "version": version,
                "employeeIdCounters": payload.get("employeeIdCounters") or {},
            })
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return db_unavailable()
    finally:
        conn.close()


def _idempotency_key_from_request(environ, data: dict) -> str:
    headers = environ or {}
    for hk in ("HTTP_X_IDEMPOTENCY_KEY", "HTTP_IDEMPOTENCY_KEY"):
        raw = str(headers.get(hk) or "").strip()
        if raw:
            return raw[:160]
    return str(
        (data or {}).get("client_request_id")
        or (data or {}).get("idempotency_key")
        or ""
    ).strip()[:160]


def handle_create_employee(environ):
    """
    Атомарное создание сотрудника: reserve ID + запись карточки в одной транзакции (FOR UPDATE).
    Повтор с тем же client_request_id / Idempotency-Key не создаёт вторую карточку.
    """
    raw = read_body(environ)
    try:
        data = json.loads(raw.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return json_bytes({"error": "invalid json"}, "400 Bad Request")
    if not isinstance(data, dict):
        return json_bytes({"error": "body must be an object"}, "400 Bad Request")
    dept = str(data.get("department_id") or data.get("dept") or data.get("code") or "").strip()
    if not employee_id_prefix_for_dept(dept):
        return json_bytes({"error": "invalid department"}, "400 Bad Request")
    name = str(data.get("name") or "").strip()
    if not name:
        return json_bytes({"error": "name is required"}, "400 Bad Request")
    client_req = _idempotency_key_from_request(environ, data)
    if not client_req:
        return json_bytes({"error": "client_request_id is required"}, "400 Bad Request")
    user_id, err = require_session(environ)
    if err:
        return err
    if not db_configured():
        return db_unavailable()
    try:
        conn = connect()
    except Exception:
        return db_unavailable()
    try:
        with conn.cursor() as cur:
            state = fetch_state_for_update(cur)
            payload = state.get("payload") if isinstance(state.get("payload"), dict) else {}
            payload = _clone(payload) if payload else {}
            user = current_user_from_state(environ, payload, user_id)
            if not user:
                conn.rollback()
                return json_bytes({"error": "unauthorized"}, "401 Unauthorized")
            if not is_app_admin(user):
                if not can_write_dept_employees(user):
                    conn.rollback()
                    return json_bytes({"error": "forbidden"}, "403 Forbidden")
                if dept not in user_dept_codes(user):
                    conn.rollback()
                    return json_bytes({"error": "forbidden"}, "403 Forbidden")

            idem = payload.get("employeeCreateIdempotency")
            if not isinstance(idem, dict):
                idem = {}
            else:
                idem = dict(idem)
            prev = idem.get(client_req)
            if isinstance(prev, dict) and prev.get("employee_id") and prev.get("department_id"):
                prev_dept = str(prev.get("department_id"))
                prev_eid = str(prev.get("employee_id"))
                existing = find_employee_in_payload(payload, prev_dept, prev_eid)
                if existing:
                    try:
                        version = int(state.get("version") or 0)
                    except (TypeError, ValueError):
                        version = 0
                    conn.rollback()
                    return json_bytes({
                        "ok": True,
                        "idempotent": True,
                        "employee_id": prev_eid,
                        "department_id": prev_dept,
                        "employee": _clone(existing),
                        "version": version,
                        "employeeIdCounters": payload.get("employeeIdCounters") or {},
                    })
                # Ключ был, карточки уже нет (удалили) — не переиспользуем ключ для нового человека.

            ws = _find_workspace(payload.get("workspaces"), dept)
            if not isinstance(ws, dict):
                conn.rollback()
                return json_bytes({"error": "department not found"}, "400 Bad Request")
            dept_name = str(ws.get("name") or data.get("department") or dept).strip()
            eid, alloc_err = allocate_employee_id_in_payload(payload, dept)
            if alloc_err or not eid:
                conn.rollback()
                return json_bytes({"error": alloc_err or "allocate failed"}, "400 Bad Request")
            # Глобальная уникальность: id не должен уже быть в любом отделе.
            counts = collect_employee_id_dept_counts(payload)
            if eid in counts:
                conn.rollback()
                return json_bytes({"error": "duplicate employee id"}, "400 Bad Request")
            employee = build_created_employee_record(eid, data, dept, dept_name)
            if not isinstance(ws.get("employees"), list):
                ws["employees"] = []
            if not isinstance(ws.get("employeeIds"), list):
                ws["employeeIds"] = []
            ws["employees"].append(employee)
            if employee.get("jobStatus") != "fired" and eid not in [str(x) for x in ws["employeeIds"]]:
                ws["employeeIds"].append(eid)
            idem[client_req] = {
                "employee_id": eid,
                "department_id": dept,
            }
            payload["employeeCreateIdempotency"] = idem
            _trim_employee_create_idempotency(payload)
            try:
                expected = int(state.get("version") or 0)
            except (TypeError, ValueError):
                expected = 0
            updated_by = str(user.get("user_id") or "")[:120]
            row = persist_payload(cur, payload, expected, updated_by)
            if not row:
                conn.rollback()
                return json_bytes(
                    {"error": "conflict", "message": "Повторите создание сотрудника."},
                    "409 Conflict",
                )
            conn.commit()
            version = int(row[0] or 0)
            return json_bytes({
                "ok": True,
                "idempotent": False,
                "employee_id": eid,
                "department_id": dept,
                "employee": employee,
                "version": version,
                "employeeIdCounters": payload.get("employeeIdCounters") or {},
            })
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
        elif method == "POST" and path == "/api/employee-id/reserve":
            status, headers, body = handle_reserve_employee_id(environ)
        elif method == "POST" and path == "/api/employees":
            status, headers, body = handle_create_employee(environ)
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
