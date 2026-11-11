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
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIServer, make_server

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
    # Локальный тест: только проверенный DATABASE_URL, без сборки из PG*.
    try:
        from wt_local_guard import local_test_mode_enabled

        if local_test_mode_enabled():
            from wt_local_guard import enforce_local_test_guard

            enforce_local_test_guard()
            url = _env("DATABASE_URL")
            return url
    except ImportError:
        pass
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
    try:
        from wt_local_guard import local_test_mode_enabled

        if local_test_mode_enabled():
            # В локальном тесте единственный источник — DATABASE_URL.
            return bool(_env("DATABASE_URL"))
    except ImportError:
        pass
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

    try:
        from wt_local_guard import enforce_local_test_guard, local_test_mode_enabled

        if local_test_mode_enabled():
            enforce_local_test_guard()
    except ImportError:
        pass
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


def configured_owner_user_id() -> str:
    """Явный user_id владельца из env. Пусто → удаление сотрудников запрещено всем."""
    return _env("WT_OWNER_USER_ID").strip()


def is_payload_owner(user: dict | None) -> bool:
    """Владелец = точное совпадение session user_id с WT_OWNER_USER_ID. Не все admin."""
    owner = configured_owner_user_id()
    if not owner:
        return False
    if not user or user.get("is_active") is False:
        return False
    return str(user.get("user_id") or "").strip() == owner


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


def public_session_user(user: dict | None) -> dict | None:
    """Ответ login/me: is_owner только для текущей сессии (серверный расчёт)."""
    pub = public_user(user)
    if not pub:
        return None
    pub["is_owner"] = is_payload_owner(user)
    return pub


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


def user_has_salary_pay(user: dict | None) -> bool:
    """Право «Выдача зарплаты» (отметка Выдано). Не равно расчёту начислений."""
    if is_app_admin(user):
        return True
    if not user:
        return False
    perms = user.get("permissions") or {}
    if perm_rank(perms.get("salaryPay")) > 0:
        return True
    return str(user.get("legacy_role") or "") == "pay"


def user_can_write_salary(user: dict | None) -> bool:
    """Может писать ветку salary: расчёт и/или выдача."""
    return user_has_perm(user, "salary", "edit") or user_has_salary_pay(user)


# Подразделения, чьи табели/карточки нужны для зарплатных ведомостей.
PAYROLL_DEPT_CODES = frozenset({"shop", "office", "warehouse", "drivers"})
# Поля salary.<month>.<empId>.*
SALARY_PAY_FIELD_KEYS = frozenset({"issued"})
SALARY_CALC_FIELD_KEYS = frozenset(
    {
        "fines",
        "bonus",
        "advance",
        "products",
        "taxes",
        "vacationPay",
        "vacationPaidEarlier",
        "skipFine",
        "taxiDaily",
        "sources",
        "updated_at",
        "external_1c_id",
        "reflect1c",
        "expenseZp",
        "rate",
    }
)


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
    # Планы управленческих периодов — та же зона, что plans/manualFacts (без новой роли).
    "period_plans": "managementIndicators",
    "mgmt_periods": "managementIndicators",
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
                # Без права на зарплату закрытые поля в payload игнорируем (как _overlay_open),
                # а не даём 403: клиент без salary часто шлёт пустой ensureShopPayroll().
                if allow_salary and not _same(val, cur.get(key)):
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


def _filter_metric_extra_employee_ids(
    assignment_dept: str, raw: Any, emp_dept: dict[str, str], local_ids: set[str] | None = None
) -> list[str]:
    """Доп. сотрудники назначения: как клиент normalizeMetricExtraEmployeeIds.

    Территория: другие territory-dept, не shop и не то же department_id строки.
    Магазин: только shop, не входящие в основные employee_ids.
    """
    dept = str(assignment_dept or "").strip()
    local = {str(x) for x in (local_ids or set()) if x}
    out: list[str] = []
    seen: set[str] = set()
    for emp_id in _metric_id_list(raw):
        eid = str(emp_id)
        if not eid or eid in seen or eid in local:
            continue
        home = str(emp_dept.get(eid) or "").strip()
        if not home:
            continue
        if dept == "shop":
            if home != "shop":
                continue
        elif _is_territory_assignment_dept(dept):
            if home == "shop" or home == dept or home not in TERRITORY_CODES:
                continue
        else:
            continue
        seen.add(eid)
        out.append(eid)
    return out


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
        extras = _filter_metric_extra_employee_ids(dept, row.get("extra_employee_ids"), emp_dept, set(eids))
        aid = str(row.get("assignment_id") or "").strip()
        if aid and aid in index:
            dest = result[index[aid]]
            dest_dept = str(dest.get("department_id") or "").strip()
            if dest_dept not in codes:
                continue
            dest["employee_ids"] = eids
            # extras всегда с входящего снимка (в т.ч. [] — явная очистка).
            if "extra_employee_ids" in row:
                dest["extra_employee_ids"] = extras
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
        created["extra_employee_ids"] = extras
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


def _metric_catalog_applied_ids(before: Any, after: Any, incoming: Any) -> list[str]:
    """
    ID показателей из incoming.shopMetricCatalog.items, которые после PUT
    реально есть на сервере с тем же name (подтверждение без полного payload).
    Идемпотентный повтор после timeout: item уже в before/after — всё равно в списке.
    """
    if not isinstance(incoming, dict):
        return []
    inc_cat = incoming.get("shopMetricCatalog")
    if not isinstance(inc_cat, dict):
        return []
    after_cat = after.get("shopMetricCatalog") if isinstance(after, dict) else {}
    after_items = after_cat.get("items") if isinstance(after_cat, dict) else []
    after_by_id = {
        str(row.get("id")): row
        for row in (after_items or [])
        if isinstance(row, dict) and row.get("id") not in (None, "")
    }
    applied: list[str] = []
    for item in inc_cat.get("items") or []:
        if not isinstance(item, dict) or item.get("id") in (None, ""):
            continue
        iid = str(item.get("id"))
        got = after_by_id.get(iid)
        if not isinstance(got, dict):
            continue
        if str(got.get("name") or "") != str(item.get("name") or ""):
            continue
        applied.append(iid)
    return applied


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
        can_incoming = _user_can_touch_metric_item(item, codes)
        can_existing = existing is not None and _user_can_touch_metric_item(existing, codes)
        if not can_incoming and not can_existing:
            # Явная попытка создать/изменить недоступную метрику → 403 (не silent skip).
            # Неизменённый чужой item в полном каталоге (_same) не запрещаем.
            if existing is None or not _same(existing, item):
                return True
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
        # salary — полный payroll; metricsSetup — связка метрик магазина (деньги KPI режет _shop_payroll_kpi_money_forbidden).
        return "shop" in codes and (
            user_has_perm(user, "salary", "edit") or _can_edit_metric_setup(user)
        )
    if key == "salary":
        return user_can_write_salary(user)
    if key == "financeRec":
        return user_has_perm(user, "financeRec", "edit") or user_has_perm(user, "financeRecSetup", "edit")
    if key == "management":
        return _can_write_any_management(user)
    if key == "orgStructure":
        return user_has_perm(user, "orgStructure", "edit")
    if key == "customerReturns":
        return user_has_perm(user, "customerReturns", "edit")
    return False


def _salary_record_field_forbidden(field: str, can_calc: bool, can_pay: bool) -> bool:
    if field in SALARY_PAY_FIELD_KEYS:
        return not can_pay
    if can_calc:
        return False
    return True


def _salary_record_diff_forbidden(current: Any, incoming: Any, can_calc: bool, can_pay: bool) -> bool:
    if not isinstance(incoming, dict):
        return True
    cur = current if isinstance(current, dict) else {}
    for field, val in incoming.items():
        if _same(val, cur.get(field)):
            continue
        if _salary_record_field_forbidden(str(field), can_calc, can_pay):
            return True
    return False


def _salary_branch_forbidden(current: Any, incoming: Any, user: dict) -> bool:
    """Запрет записи salary: Calc — начисления; Pay — только issued."""
    can_calc = user_has_perm(user, "salary", "edit")
    can_pay = user_has_salary_pay(user)
    if not can_calc and not can_pay:
        return True
    if not isinstance(incoming, dict):
        return True
    cur_root = current if isinstance(current, dict) else {}
    for month, bucket in incoming.items():
        if not isinstance(bucket, dict):
            return True
        cur_month = cur_root.get(month) if isinstance(cur_root.get(month), dict) else {}
        for emp_id, rec in bucket.items():
            if _salary_record_diff_forbidden(cur_month.get(emp_id), rec, can_calc, can_pay):
                return True
    return False


def _merge_salary_for_user(current: Any, incoming: Any, user: dict) -> Any:
    """Слить salary с учётом Calc/Pay: pay-only не трогает начисления."""
    can_calc = user_has_perm(user, "salary", "edit")
    can_pay = user_has_salary_pay(user)
    if not isinstance(incoming, dict):
        return current
    out = _clone(current) if isinstance(current, dict) else {}
    for month, bucket in incoming.items():
        if not isinstance(bucket, dict):
            continue
        out_month = out.get(month) if isinstance(out.get(month), dict) else {}
        out_month = _clone(out_month)
        for emp_id, rec in bucket.items():
            if not isinstance(rec, dict):
                continue
            cur = out_month.get(emp_id) if isinstance(out_month.get(emp_id), dict) else {}
            nxt = _clone(cur)
            for field, val in rec.items():
                key = str(field)
                if key in SALARY_PAY_FIELD_KEYS:
                    if can_pay:
                        nxt[key] = _clone(val)
                elif can_calc:
                    nxt[key] = _clone(val)
            out_month[str(emp_id)] = nxt
        out[str(month)] = out_month
    return out


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


def _timesheet_emp_has_activity(days: Any) -> bool:
    """Есть ли в строке табеля сотрудника хотя бы одна осмысленная ячейка."""
    if not isinstance(days, dict):
        return bool(days)
    for rec in days.values():
        if not isinstance(rec, dict):
            if rec not in (None, "", 0, False):
                return True
            continue
        if str(rec.get("status") or "").strip():
            return True
        try:
            if float(rec.get("hours") or 0):
                return True
        except (TypeError, ValueError):
            pass
        try:
            if int(rec.get("lateMinutes") or 0):
                return True
        except (TypeError, ValueError):
            pass
    return False


def _salary_record_meaningful(rec: Any) -> bool:
    """True только при значимых полях; пустой {} / нули / флаги false — техническая заглушка."""
    if rec is None:
        return False
    if not isinstance(rec, dict):
        return bool(rec)
    money_keys = (
        "fines", "bonus", "advance", "products", "taxiDaily",
        "vacationPay", "taxes", "reflect1c", "expenseZp",
    )
    for key in money_keys:
        try:
            if float(rec.get(key) or 0):
                return True
        except (TypeError, ValueError):
            if rec.get(key) not in (None, ""):
                return True
    if rec.get("issued") is True:
        return True
    if rec.get("vacationPaidEarlier") is True:
        return True
    if rec.get("skipFine") not in (None, ""):
        return True
    if str(rec.get("updated_at") or "").strip():
        return True
    if str(rec.get("external_1c_id") or "").strip():
        return True
    sources = rec.get("sources")
    if isinstance(sources, dict) and sources:
        return True
    known = set(money_keys) | {
        "issued", "vacationPaidEarlier", "skipFine", "updated_at",
        "external_1c_id", "sources",
    }
    for key, val in rec.items():
        if key in known:
            continue
        if val in (None, "", 0, False, [], {}):
            continue
        # Неизвестное поле с данными — безопаснее блокировать.
        return True
    return False


def _vacation_plan_row_meaningful(row: Any) -> bool:
    """Пустая строка плана (без даты/суммы/истории) — заглушка; иначе блокировка."""
    if not isinstance(row, dict):
        return False
    if str(row.get("planned_date") or "").strip():
        return True
    amt = row.get("planned_amount")
    if amt not in (None, ""):
        try:
            if float(amt) != 0:
                return True
        except (TypeError, ValueError):
            return True
    status = str(row.get("status") or "").strip().lower()
    if status and status not in ("planned", ""):
        return True
    if str(row.get("comment") or "").strip():
        return True
    hist = row.get("history")
    if isinstance(hist, list) and len(hist) > 0:
        return True
    return False


def _access_list_meaningful(val: Any) -> bool:
    """workAccess/extraAccess: [] не блок; непустой список или неожиданная форма — блок."""
    if val is None:
        return False
    if isinstance(val, list):
        return any(str(x or "").strip() for x in val)
    if isinstance(val, dict):
        return bool(val)
    return bool(val)


def _metric_emp_payload_meaningful(val: Any) -> bool:
    """Факты метрик по сотруднику: пустой {} не блок; иначе при сомнении — блок."""
    if val is None or val == "" or val is False:
        return False
    if isinstance(val, list):
        return len(val) > 0
    if not isinstance(val, dict):
        return True
    if not val:
        return False
    for cell in val.values():
        if cell in (None, "", 0, False, [], {}):
            continue
        if isinstance(cell, dict) and not cell:
            continue
        return True
    return False


# Известные поля ячеек метрик с прямой ссылкой на сотрудника (не универсальный обход).
_METRIC_CELL_EMPLOYEE_FIELDS = (
    "employee_id",
    "conducted_by_employee_id",
    "assigned_cashier_employee_id",
    "actual_cashier_employee_id",
    "replaced_employee_id",
)


def _cell_refs_employee(cell: Any, eid: str) -> bool:
    if str(cell or "").strip() == eid:
        return True
    if not isinstance(cell, dict):
        return False
    for field in _METRIC_CELL_EMPLOYEE_FIELDS:
        if str(cell.get(field) or "").strip() == eid:
            return True
    # История смены ответственного пересчёта: from_employee_id / to_employee_id.
    for hist in cell.get("history") or []:
        if not isinstance(hist, dict):
            continue
        if str(hist.get("from_employee_id") or "").strip() == eid:
            return True
        if str(hist.get("to_employee_id") or "").strip() == eid:
            return True
    return False


def _metric_branch_links_employee(branch: Any, eid: str) -> list[str]:
    """Метки связей сотрудника внутри shopMetrics / deptMetrics.<dept>."""
    out: list[str] = []
    if not isinstance(branch, dict) or not eid:
        return out
    wa = branch.get("workAccess")
    if isinstance(wa, dict):
        for mmap in wa.values():
            if isinstance(mmap, dict) and eid in mmap and _access_list_meaningful(mmap.get(eid)):
                out.append("назначения метрик")
                break
    ea = branch.get("extraAccess")
    if isinstance(ea, dict) and eid in ea and _access_list_meaningful(ea.get(eid)):
        if "назначения метрик" not in out:
            out.append("назначения метрик")
    for key in METRIC_INPUT_KEYS:
        store = branch.get(key)
        if not isinstance(store, dict):
            continue
        hit = False
        for sub in store.values():
            if isinstance(sub, list):
                for cell in sub:
                    if _cell_refs_employee(cell, eid):
                        hit = True
                        break
                if hit:
                    break
                continue
            if isinstance(sub, dict) and eid in sub and _metric_emp_payload_meaningful(sub.get(eid)):
                hit = True
                break
            if not isinstance(sub, dict):
                continue
            # inventoryResults / dayRoles: месяц → день → ячейка или список ролей.
            if _cell_refs_employee(sub, eid):
                hit = True
                break
            for cell in sub.values():
                if isinstance(cell, list):
                    for item in cell:
                        if _cell_refs_employee(item, eid):
                            hit = True
                            break
                    if hit:
                        break
                elif _cell_refs_employee(cell, eid):
                    hit = True
                    break
            if hit:
                break
        if hit:
            out.append("KPI/метрики")
            break
    return out


def _sales_plan_root_links_employee(root: Any, eid: str) -> bool:
    if not isinstance(root, dict) or not eid:
        return False
    months = root.get("months")
    if not isinstance(months, dict):
        return False
    for rec in months.values():
        if not isinstance(rec, dict):
            continue
        emap = rec.get("employees")
        if isinstance(emap, dict) and eid in emap:
            return True
    return False


def _customer_returns_links_employee(root: Any, eid: str) -> bool:
    if not isinstance(root, dict) or not eid:
        return False
    fields = (
        "sales_author_employee_id",
        "driver_employee_id",
        "cashier_employee_id",
        "picker_employee_id",
    )
    for item in root.get("items") or []:
        if not isinstance(item, dict):
            continue
        for field in fields:
            if str(item.get(field) or "").strip() == eid:
                return True
    return False


def _revision_root_links_employee(root: Any, eid: str) -> bool:
    if not isinstance(root, dict) or not eid:
        return False
    for group in root.get("groups") or []:
        if not isinstance(group, dict):
            continue
        if str(group.get("employee_id") or group.get("employeeId") or "").strip() == eid:
            return True
    tasks = root.get("tasks")
    if not isinstance(tasks, dict):
        return False
    task_fields = (
        "employee_id",
        "employeeId",
        "assignedEmployeeId",
        "completedEmployeeId",
    )
    for month_map in tasks.values():
        if not isinstance(month_map, dict):
            continue
        for task in month_map.values():
            if not isinstance(task, dict):
                continue
            for field in task_fields:
                if str(task.get(field) or "").strip() == eid:
                    return True
    return False


def _id_list_contains(raw: Any, eid: str) -> bool:
    if not isinstance(raw, list):
        return False
    return any(str(x).strip() == eid for x in raw)


def _management_links_employee(root: Any, eid: str) -> bool:
    if not isinstance(root, dict) or not eid:
        return False
    for ind in root.get("indicators") or []:
        if not isinstance(ind, dict):
            continue
        if str(ind.get("owner_employee_id") or "").strip() == eid:
            return True
        for hist in ind.get("owner_history") or []:
            if isinstance(hist, dict) and str(hist.get("employee_id") or "").strip() == eid:
                return True
    for node in root.get("spuOrgNodes") or []:
        if not isinstance(node, dict):
            continue
        if str(node.get("responsible_employee_id") or "").strip() == eid:
            return True
        if str(node.get("manager_employee_id") or "").strip() == eid:
            return True
    heads = root.get("deptHeads")
    if isinstance(heads, dict):
        for val in heads.values():
            if str(val or "").strip() == eid:
                return True
    for row in root.get("spuResponsibilities") or []:
        if isinstance(row, dict) and str(row.get("employee_id") or "").strip() == eid:
            return True
    for task in root.get("tasks") or []:
        if not isinstance(task, dict):
            continue
        if str(task.get("responsible_employee_id") or "").strip() == eid:
            return True
        if str(task.get("created_by_employee_id") or "").strip() == eid:
            return True
    for meet in root.get("meetings") or []:
        if not isinstance(meet, dict):
            continue
        if str(meet.get("owner_employee_id") or "").strip() == eid:
            return True
        if _id_list_contains(meet.get("participant_employee_ids"), eid):
            return True
    weeks = root.get("meetingWeeks")
    if isinstance(weeks, dict):
        for meta in weeks.values():
            if isinstance(meta, dict) and _id_list_contains(meta.get("participant_employee_ids"), eid):
                return True
    return False


def _org_structure_links_employee(root: Any, eid: str) -> bool:
    if not isinstance(root, dict) or not eid:
        return False
    for node in root.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        if str(node.get("manager_employee_id") or "").strip() == eid:
            return True
        if str(node.get("deputy_employee_id") or "").strip() == eid:
            return True
    for row in root.get("leadership") or []:
        if isinstance(row, dict) and str(row.get("employee_id") or "").strip() == eid:
            return True
    return False


def employee_linked_history_labels(payload: dict, employee_id: str) -> list[str]:
    """
    Человекочитаемые метки связанных данных сотрудника (по employee_id).
    Пустые технические заглушки (workAccess [], пустой salary {}, пустой vacation) не блокируют.
    При сомнении в значимости — блокировка сохраняется.
    """
    eid = str(employee_id or "").strip()
    if not eid or not isinstance(payload, dict):
        return []
    labels: list[str] = []
    seen: set[str] = set()

    def add(label: str) -> None:
        if label and label not in seen:
            seen.add(label)
            labels.append(label)

    for ws in payload.get("workspaces") or []:
        if not isinstance(ws, dict):
            continue
        code = str(ws.get("code") or "")
        ts = ws.get("timesheet")
        if not isinstance(ts, dict):
            continue
        for month, mmap in ts.items():
            if isinstance(mmap, dict) and eid in mmap and _timesheet_emp_has_activity(mmap.get(eid)):
                add("табель" + (f" ({code}/{month})" if code or month else ""))
                break

    salary = payload.get("salary")
    if isinstance(salary, dict):
        for month, bucket in salary.items():
            if isinstance(bucket, dict) and eid in bucket and _salary_record_meaningful(bucket.get(eid)):
                add(f"зарплата ({month})")
                break

    rep = payload.get("repSales")
    if isinstance(rep, dict):
        for month, bucket in rep.items():
            if isinstance(bucket, dict) and eid in bucket and _salary_record_meaningful(bucket.get(eid)):
                # для ТП достаточно наличия непустой записи продаж
                add(f"продажи ТП ({month})")
                break
            if isinstance(bucket, dict) and eid in bucket:
                cell = bucket.get(eid)
                if cell not in (None, {}, []):
                    add(f"продажи ТП ({month})")
                    break

    for label in _metric_branch_links_employee(payload.get("shopMetrics"), eid):
        add(label)
    dept_metrics = payload.get("deptMetrics")
    if isinstance(dept_metrics, dict):
        for branch in dept_metrics.values():
            for label in _metric_branch_links_employee(branch, eid):
                add(label)

    cat = payload.get("shopMetricCatalog")
    if isinstance(cat, dict):
        for item in cat.get("items") or []:
            if not isinstance(item, dict):
                continue
            mid = str(item.get("id") or item.get("name") or "")
            for row in item.get("assignments") or []:
                if not isinstance(row, dict):
                    continue
                extras = row.get("extra_employee_ids") or []
                ids = row.get("employee_ids") or []
                if any(str(x).strip() == eid for x in extras) or any(str(x).strip() == eid for x in ids):
                    add("назначения метрик" + (f" (каталог {mid})" if mid else ""))
                    break
            if any(x.startswith("назначения метрик") for x in seen):
                break

    payroll = payload.get("shopPayroll")
    if isinstance(payroll, dict):
        for row in payroll.get("assignments") or []:
            if isinstance(row, dict) and str(row.get("employee_id") or "").strip() == eid:
                add("KPI/премии (назначение)")
                break
        for row in payroll.get("salaries") or []:
            if isinstance(row, dict) and str(row.get("employee_id") or "").strip() == eid:
                add("KPI/премии (оклад)")
                break
        manual = payroll.get("manual")
        if isinstance(manual, dict):
            if eid in manual and _salary_record_meaningful(manual.get(eid)):
                add("KPI/премии (ручные)")
            else:
                for month, month_map in manual.items():
                    if isinstance(month_map, dict) and eid in month_map:
                        cell = month_map.get(eid)
                        if _salary_record_meaningful(cell) or (
                            cell not in (None, {}, []) and not isinstance(cell, dict)
                        ):
                            add(f"KPI/премии (ручные {month})")
                            break

    vp = payload.get("vacationPlans")
    if isinstance(vp, dict):
        for year, year_list in vp.items():
            if not isinstance(year_list, list):
                continue
            for row in year_list:
                if (
                    isinstance(row, dict)
                    and str(row.get("employee_id") or "").strip() == eid
                    and _vacation_plan_row_meaningful(row)
                ):
                    add(f"график отпусков ({year})")
                    break
            if any(x.startswith("график отпусков") for x in seen):
                break

    if _sales_plan_root_links_employee(payload.get("shopSalesPlan"), eid):
        add("план продаж")
    dept_plans = payload.get("deptSalesPlans")
    if isinstance(dept_plans, dict):
        for root in dept_plans.values():
            if _sales_plan_root_links_employee(root, eid):
                add("план продаж")
                break

    for user in payload.get("users") or []:
        if isinstance(user, dict) and str(user.get("employee_id") or "").strip() == eid:
            add("учётная запись")
            break

    if _customer_returns_links_employee(payload.get("customerReturns"), eid):
        add("возвраты покупателей")

    if _revision_root_links_employee(payload.get("shopRevision"), eid):
        add("ревизии")
    dept_revs = payload.get("deptRevisions")
    if isinstance(dept_revs, dict):
        for rev_root in dept_revs.values():
            if _revision_root_links_employee(rev_root, eid):
                add("ревизии")
                break

    if _management_links_employee(payload.get("management"), eid):
        add("СПУ/совещания")

    if _org_structure_links_employee(payload.get("orgStructure"), eid):
        add("оргструктура")

    return labels


def employee_removal_block_message(payload: dict, employee_id: str) -> str | None:
    labels = employee_linked_history_labels(payload, employee_id)
    if not labels:
        return None
    return (
        "Нельзя удалить сотрудника: есть связанные данные ("
        + ", ".join(labels)
        + "). Измените статус на «Уволен» и снимите галочку «В табель»."
    )


def collect_incoming_employee_removals(incoming: dict) -> list[dict[str, str]]:
    """employeeRemovals + removedEmployeeIds внутри workspaces."""
    items = list(_normalize_employee_removals((incoming or {}).get("employeeRemovals")))
    seen = {(row["department_id"], row["employee_id"]) for row in items}
    for ws in (incoming or {}).get("workspaces") or []:
        if not isinstance(ws, dict):
            continue
        code = str(ws.get("code") or "").strip()
        if not code or code == "summary":
            continue
        local_rm = ws.get("removedEmployeeIds")
        if not isinstance(local_rm, list):
            continue
        for raw in local_rm:
            eid = str(raw or "").strip()
            if not eid:
                continue
            key = (code, eid)
            if key in seen:
                continue
            seen.add(key)
            items.append({"department_id": code, "employee_id": eid})
    return items


def employee_removals_denied(current: dict, incoming: dict) -> str | None:
    """Блокировка PUT-удаления карточки при связанных данных на сервере."""
    for item in collect_incoming_employee_removals(incoming if isinstance(incoming, dict) else {}):
        msg = employee_removal_block_message(current if isinstance(current, dict) else {}, item["employee_id"])
        if msg:
            return "employee_has_history"
    return None


def employee_delete_owner_denied(user: dict | None, incoming: dict | None = None) -> str | None:
    """
    PUT: если в запросе есть удаление карточек — только WT_OWNER_USER_ID.
    Без переменной или чужая сессия → employee_delete_forbidden.
    """
    items = collect_incoming_employee_removals(incoming if isinstance(incoming, dict) else {})
    if not items:
        return None
    if not is_payload_owner(user):
        return "employee_delete_forbidden"
    return None


def apply_employee_removals(payload: dict, removals: Any, allowed_depts: set[str] | None = None) -> None:
    """Удаляет карточки только по явному списку. Не трогает чужие отделы вне allowed_depts.
    Не чистит salary/timesheet/KPI — только employees/employeeIds.
    Связанные данные должны быть проверены через employee_removals_denied / OPS guard.
    """
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
    outsourcing = data.get("outsourcing") is True
    try:
        fixed_pay = float(data.get("outsourcingFixedPay") if data.get("outsourcingFixedPay") is not None else data.get("rate") or 0)
    except (TypeError, ValueError):
        fixed_pay = 0.0
    if fixed_pay < 0:
        fixed_pay = 0.0
    rate_val = fixed_pay if outsourcing else 0.0
    terms: dict = {}
    raw_terms = data.get("terms")
    if isinstance(raw_terms, dict):
        for mk, mv in raw_terms.items():
            key = str(mk or "").strip()
            if not key or not isinstance(mv, dict):
                continue
            try:
                t_rate = float(mv.get("rate") or 0)
            except (TypeError, ValueError):
                t_rate = 0.0
            try:
                t_taxi = float(mv.get("taxiDaily") or 0)
            except (TypeError, ValueError):
                t_taxi = 0.0
            terms[key] = {"rate": t_rate if t_rate > 0 else rate_val, "taxiDaily": t_taxi}
    if outsourcing and rate_val > 0 and not terms:
        # Без месяца от клиента — только карточка; клиент допишет terms текущего месяца.
        pass
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
        "rate": rate_val,
        "taxiDaily": 0,
        "jobStatus": job_status,
        "fired_on": str(data.get("fired_on") or "").strip()[:10] if job_status == "fired" else "",
        "participates_in_spu_meetings": meet_on,
        "meeting_participant": meet_on,
        "outsourcing": outsourcing,
        "outsourcingFixedPay": rate_val if outsourcing else 0,
        "terms": terms,
    }
    return rec


def _merge_admin_payload(current: dict, incoming: dict) -> dict:
    stored = _clone(current)
    for key, val in incoming.items():
        if key in WT_OPS_META_KEYS or key in ("employeeRemovals", "employeeCreateIdempotency"):
            # Removals применяются отдельно; _wt_* / idempotency — только сервер.
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
        elif key == "shopMetricCatalog":
            # Узкий PUT одной метрики не должен затирать весь справочник (merge по id).
            if _empty_wipe(val, stored.get(key)):
                continue
            stored[key] = _overlay_open(stored.get(key), val, False)
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
        # Служебные карты версий/идемпотентности: не право записи раздела.
        # Клиент часто шлёт отфильтрованный _wt_records (подмножество) — это не 403.
        # Фактические версии остаются серверными через protect_wt_records_on_put.
        if key in WT_OPS_META_KEYS:
            continue
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
                # Пустой/обрезанный клиентский снимок (нет shop в GET) — не 403.
                if _empty_wipe(val, current.get(key)) or _same(val, current.get(key)):
                    continue
                return True
            if _metric_root_forbidden(current.get(key), val, user):
                return True
            continue
        if key == "shopMetricCatalog":
            if _catalog_forbidden(current.get(key) if isinstance(current.get(key), dict) else {}, val, user):
                return True
            continue
        if key == "shopPayroll":
            if not _can_write_top(user, key):
                if _empty_wipe(val, current.get(key)) or _same(val, current.get(key)):
                    continue
                return True
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
                if _empty_wipe(val, current.get(key)) or _same(val, current.get(key)):
                    continue
                return True
            if _finance_directory_forbidden(current.get(key), val, user):
                return True
            if user_has_perm(user, "financeRec", "edit") and isinstance(val, dict):
                cur_fin = current.get(key) if isinstance(current.get(key), dict) else {}
                for data_key in FINANCE_DATA_KEYS:
                    if data_key in val and _closed_field_violations(cur_fin.get(data_key), val.get(data_key), True):
                        return True
            continue
        if key == "salary":
            if not user_can_write_salary(user):
                if _empty_wipe(val, current.get(key)) or _same(val, current.get(key)):
                    continue
                return True
            if _salary_branch_forbidden(current.get(key), val, user):
                return True
            continue
        if _can_write_top(user, key):
            allow_sal = user_has_perm(user, "salary", "edit") or key == "financeRec"
            if _closed_field_violations(current.get(key), val, allow_sal):
                return True
            continue
        # Нет права на ключ: пустой срез / совпадение — игнор; иное отличие — 403.
        if _empty_wipe(val, current.get(key)) or _same(val, current.get(key)):
            continue
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
        if key == "salary":
            stored[key] = _merge_salary_for_user(stored.get(key), incoming.get(key), user)
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


# ---------------------------------------------------------------------------
# Точечные операции сохранения (многопользовательский фундамент).
# Метаданные живут внутри app_state JSON — без миграции схемы PostgreSQL.
# ---------------------------------------------------------------------------
WT_RECORDS_KEY = "_wt_records"
WT_IDEMPOTENCY_KEY = "_wt_idempotency"
WT_OPS_META_KEYS = frozenset({WT_RECORDS_KEY, WT_IDEMPOTENCY_KEY})
# Сегмент может быть обычным id или спец-операцией вида $bulk (не поле объекта).
WT_PATH_RE = re.compile(
    r"^[A-Za-z0-9_-]+(?:\.(?:\$[A-Za-z0-9_-]+|[A-Za-z0-9_-]+)){0,15}$"
)
# Финконтроль: id-адресация массивов + виртуальный bulk-upsert операций.
FR_LIST_ID_FIELDS = {
    "operations": "finance_operation_id",
    "audit": "audit_id",
    "import_batches": "import_batch_id",
    "money_transits": "transit_id",
}
FR_OPS_BULK_SEGMENT = "$bulk"
FR_OPS_BULK_PATH = "financeRec.operations.$bulk"
FR_OPS_BULK_MAX_ITEMS = 2000
# Строки остатков и статусов без собственного id: адрес — смысловой ключ из существующих полей
# (объект + неделя/дата + счёт). Тот же ключ считает клиент (frRowKey в fr-mod.js).
FR_ROW_KEY_LISTS = ("openings", "day_closings", "period_states")
_FR_ROW_KEY_ISO_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}")
_FR_ROW_KEY_DMY_RE = re.compile(r"^([0-9]{1,2})[./-]([0-9]{1,2})[./-]([0-9]{2,4})")


def _fr_row_key_date(raw: Any) -> str:
    s = "" if raw is None else str(raw)
    s = s.replace("\u00a0", " ").replace("\u202f", " ").strip()
    if _FR_ROW_KEY_ISO_RE.match(s):
        return s[:10]
    m = _FR_ROW_KEY_DMY_RE.match(s)
    if m:
        y = int(m.group(3))
        if y < 100:
            y += 2000
        return "%d-%02d-%02d" % (y, int(m.group(2)), int(m.group(1)))
    return s


def _fr_row_key_part(value: Any) -> str:
    s = "" if value is None else str(value)
    out = []
    for b in s.encode("utf-8"):
        c = chr(b)
        if ("0" <= c <= "9") or ("A" <= c <= "Z") or ("a" <= c <= "z"):
            out.append(c)
        else:
            out.append("-%02X" % b)
    return "".join(out)


def fr_row_key(list_key: str, row: Any) -> str:
    """Сегмент пути строки openings/day_closings/period_states; "" — ключ неполный."""
    if not isinstance(row, dict):
        return ""
    if list_key == "openings":
        raw = [
            row.get("object_id") or row.get("finance_area_id"),
            row.get("period_key"),
            row.get("account_id") or row.get("finance_source_id"),
        ]
    elif list_key == "day_closings":
        raw = [
            row.get("object_id") or row.get("finance_area_id"),
            _fr_row_key_date(row.get("date")),
            row.get("account_id") or row.get("finance_source_id"),
        ]
    elif list_key == "period_states":
        # Только finance_area_id — как клиентский frPeriodState; строка лишь с object_id не адресуется.
        raw = [row.get("finance_area_id"), row.get("period_key")]
    else:
        return ""
    parts = ["" if v is None else str(v) for v in raw]
    if any(not p for p in parts):
        return ""
    return "_".join(_fr_row_key_part(p) for p in parts)


def _fr_row_by_key(items: Any, list_key: str, key: str) -> tuple[int, dict | None]:
    """Первая строка с ключом (как find в клиенте); дубли не трогаем."""
    if not isinstance(items, list) or not key:
        return -1, None
    for idx, row in enumerate(items):
        if isinstance(row, dict) and fr_row_key(list_key, row) == key:
            return idx, row
    return -1, None
WT_IDEMPOTENCY_MAX = 300


def ops_enabled() -> bool:
    """WT_OPS_ENABLED=0 отключает POST /api/state/op без потери уже сохранённых данных."""
    return _env("WT_OPS_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off")


def normalize_op_path(raw: Any) -> str | None:
    path = str(raw or "").strip()
    if not path or len(path) > 400:
        return None
    if not WT_PATH_RE.match(path):
        return None
    top = path.split(".", 1)[0]
    if top in WT_OPS_META_KEYS or top.startswith("_"):
        return None
    return path


def _workspace_by_code(workspaces: Any, code: str) -> tuple[int, dict | None]:
    """workspaces хранится списком; путь ops использует code, не индекс."""
    if not isinstance(workspaces, list):
        return -1, None
    want = str(code or "")
    for idx, ws in enumerate(workspaces):
        if isinstance(ws, dict) and str(ws.get("code") or "") == want:
            return idx, ws
    return -1, None


def _list_item_by_id(items: Any, item_id: str, id_field: str = "id") -> tuple[int, dict | None]:
    """Список dict — путь ops использует id_field, не индекс."""
    if not isinstance(items, list):
        return -1, None
    want = str(item_id or "")
    field = str(id_field or "id")
    for idx, row in enumerate(items):
        if isinstance(row, dict) and str(row.get(field) or "") == want:
            return idx, row
    return -1, None


def get_path_value(root: Any, path: str) -> Any:
    if not path:
        return None
    parts = path.split(".")
    cur = root
    i = 0
    while i < len(parts):
        part = parts[i]
        # workspaces.<code>.… — список подразделений, не dict по code.
        if (
            part == "workspaces"
            and isinstance(cur, dict)
            and isinstance(cur.get("workspaces"), list)
        ):
            if i + 1 >= len(parts):
                return cur.get("workspaces")
            idx, ws = _workspace_by_code(cur.get("workspaces"), parts[i + 1])
            if idx < 0 or not isinstance(ws, dict):
                return None
            cur = ws
            i += 2
            continue
        # …employees.<id>.… — список сотрудников по id.
        if (
            part in WT_WORKSPACE_ID_LISTS
            and isinstance(cur, dict)
            and isinstance(cur.get(part), list)
            and i + 1 < len(parts)
        ):
            idx, row = _list_item_by_id(cur.get(part), parts[i + 1])
            if idx < 0 or not isinstance(row, dict):
                return None
            cur = row
            i += 2
            continue
        # users.<user_id>.… — список пользователей по user_id.
        if (
            part == "users"
            and isinstance(cur, dict)
            and isinstance(cur.get("users"), list)
            and i + 1 < len(parts)
        ):
            want = str(parts[i + 1])
            found = None
            for row in cur.get("users") or []:
                if isinstance(row, dict) and str(row.get("user_id") or "") == want:
                    found = row
                    break
            if found is None:
                return None
            cur = found
            i += 2
            continue
        # vacationPlans.<year>.<vacation_plan_id>.… — год остаётся массивом.
        if (
            part == "vacationPlans"
            and isinstance(cur, dict)
            and isinstance(cur.get("vacationPlans"), dict)
        ):
            if i + 1 >= len(parts):
                return cur.get("vacationPlans")
            year = parts[i + 1]
            year_list = cur["vacationPlans"].get(year)
            if i + 2 >= len(parts):
                return year_list
            if not isinstance(year_list, list):
                return None
            idx, row = _list_item_by_id(year_list, parts[i + 2], "vacation_plan_id")
            if idx < 0 or not isinstance(row, dict):
                return None
            cur = row
            i += 3
            continue
        # financeRec.{openings|day_closings|period_states}.<ключ> — строка по смысловому ключу.
        if (
            part in FR_ROW_KEY_LISTS
            and i >= 1
            and parts[i - 1] == "financeRec"
            and isinstance(cur, dict)
            and isinstance(cur.get(part), list)
            and i + 1 < len(parts)
        ):
            idx, row = _fr_row_by_key(cur.get(part), part, parts[i + 1])
            if idx < 0 or not isinstance(row, dict):
                return None
            cur = row
            i += 2
            continue
        # financeRec.{operations|audit|import_batches}.<id>[.…] — массив, не dict/индекс.
        # operations.$bulk — виртуальный путь (только meta), в массиве не ищем.
        if (
            part in FR_LIST_ID_FIELDS
            and i >= 1
            and parts[i - 1] == "financeRec"
            and isinstance(cur, dict)
            and isinstance(cur.get(part), list)
            and i + 1 < len(parts)
        ):
            item_id = parts[i + 1]
            if part == "operations" and str(item_id) == FR_OPS_BULK_SEGMENT:
                return None
            idx, row = _list_item_by_id(cur.get(part), item_id, FR_LIST_ID_FIELDS[part])
            if idx < 0 or not isinstance(row, dict):
                return None
            cur = row
            i += 2
            continue
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
            i += 1
            continue
        return None
    return cur


def _set_in_id_list(parent: dict, list_key: str, item_id: str, rest: list[str], value: Any, id_field: str = "id") -> bool:
    """Пишет в parent[list_key] элемент с id_field==item_id. rest — путь внутри элемента (пустой = весь объект)."""
    items = parent.get(list_key)
    if not isinstance(items, list):
        items = []
    items = _clone(items)
    idx = -1
    row = None
    for i, cand in enumerate(items):
        if isinstance(cand, dict) and str(cand.get(id_field) or "") == str(item_id):
            idx = i
            row = cand
            break
    if not rest:
        if value is None:
            if idx >= 0:
                items.pop(idx)
        elif isinstance(value, dict):
            cloned = _clone(value)
            if id_field not in cloned:
                cloned[id_field] = item_id
            if idx >= 0:
                items[idx] = cloned
            else:
                items.append(cloned)
        else:
            return False
        parent[list_key] = items
        return True
    if idx < 0 or not isinstance(row, dict):
        if value is None:
            return True
        row = {id_field: item_id}
        items.append(row)
        idx = len(items) - 1
    row = _clone(row)
    cur: Any = row
    for part in rest[:-1]:
        nxt = cur.get(part) if isinstance(cur, dict) else None
        if not isinstance(nxt, dict):
            nxt = {}
            if isinstance(cur, dict):
                cur[part] = nxt
        cur = nxt
    leaf = rest[-1]
    if not isinstance(cur, dict):
        return False
    if value is None:
        cur.pop(leaf, None)
    else:
        cur[leaf] = _clone(value)
    items[idx] = row
    parent[list_key] = items
    return True


def set_path_value(root: dict, path: str, value: Any) -> dict:
    out = _clone(root) if isinstance(root, dict) else {}
    parts = path.split(".")
    if not parts:
        return out
    # Не превращаем список workspaces в dict при записи по code.
    if parts[0] == "workspaces" and isinstance(out.get("workspaces"), list):
        if len(parts) < 2:
            return out
        code = parts[1]
        wslist = _clone(out.get("workspaces"))
        idx, ws = _workspace_by_code(wslist, code)
        if idx < 0 or not isinstance(ws, dict):
            # Новое подразделение через op не создаём — только существующие.
            return out
        ws = _clone(ws)
        if len(parts) == 2:
            if value is None:
                wslist.pop(idx)
            elif isinstance(value, dict):
                wslist[idx] = _clone(value)
            else:
                return out
            out["workspaces"] = wslist
            return out
        # workspaces.<code>.employees.<empId>[.…]
        if len(parts) >= 4 and parts[2] in WT_WORKSPACE_ID_LISTS:
            ok = _set_in_id_list(ws, parts[2], parts[3], parts[4:], value, "id")
            if not ok:
                return out
            wslist[idx] = ws
            out["workspaces"] = wslist
            return out
        cur: Any = ws
        for part in parts[2:-1]:
            nxt = cur.get(part) if isinstance(cur, dict) else None
            if not isinstance(nxt, dict):
                nxt = {}
                if isinstance(cur, dict):
                    cur[part] = nxt
            cur = nxt
        leaf = parts[-1]
        if not isinstance(cur, dict):
            return out
        if value is None:
            cur.pop(leaf, None)
        else:
            cur[leaf] = _clone(value)
        wslist[idx] = ws
        out["workspaces"] = wslist
        return out
    # users.<user_id>[.…]
    if parts[0] == "users" and isinstance(out.get("users"), list) and len(parts) >= 2:
        ok = _set_in_id_list(out, "users", parts[1], parts[2:], value, "user_id")
        return out if ok else out
    # vacationPlans.<year>.<vacation_plan_id>[.…] — формат года остаётся массивом.
    if parts[0] == "vacationPlans" and len(parts) >= 3:
        year = parts[1]
        plan_id = parts[2]
        rest = parts[3:]
        vp = out.get("vacationPlans")
        if not isinstance(vp, dict):
            vp = {}
        else:
            vp = _clone(vp)
        items = vp.get(year)
        if not isinstance(items, list):
            items = []
        else:
            items = _clone(items)
        # Legacy: в массиве есть запись сотрудника без vacation_plan_id — не плодим дубликат.
        idx, row = _list_item_by_id(items, plan_id, "vacation_plan_id")
        if idx < 0 and isinstance(value, dict) and not rest:
            emp = str(value.get("employee_id") or "")
            if emp:
                for i, cand in enumerate(items):
                    if not isinstance(cand, dict):
                        continue
                    if str(cand.get("employee_id") or "") != emp:
                        continue
                    if str(cand.get("vacation_plan_id") or "") in ("", str(plan_id)):
                        idx = i
                        row = cand
                        break
        vp[year] = items
        if idx >= 0 and isinstance(row, dict) and str(row.get("vacation_plan_id") or "") != str(plan_id):
            # Закрепляем стабильный id на существующем слоте до _set_in_id_list.
            fixed = _clone(row)
            fixed["vacation_plan_id"] = plan_id
            items[idx] = fixed
            vp[year] = items
        ok = _set_in_id_list(vp, year, plan_id, rest, value, "vacation_plan_id")
        if not ok:
            return out
        out["vacationPlans"] = vp
        return out
    # financeRec.{openings|day_closings|period_states}.<ключ> — только строка целиком, ключ тела = ключ пути.
    if len(parts) >= 3 and parts[0] == "financeRec" and parts[1] in FR_ROW_KEY_LISTS:
        list_key = parts[1]
        row_key = str(parts[2] or "")
        fr_raw = out.get("financeRec")
        items_raw = fr_raw.get(list_key) if isinstance(fr_raw, dict) else None
        if len(parts) != 3 or not row_key or not isinstance(value, dict) or isinstance(items_raw, dict):
            return out
        if fr_row_key(list_key, value) != row_key:
            return out
        fr = _clone(fr_raw) if isinstance(fr_raw, dict) else {}
        items = _clone(items_raw) if isinstance(items_raw, list) else []
        idx, _row = _fr_row_by_key(items, list_key, row_key)
        if idx >= 0:
            items[idx] = _clone(value)
        else:
            items.append(_clone(value))
        fr[list_key] = items
        out["financeRec"] = fr
        return out
    # financeRec.{operations|audit|import_batches}.<id>[.…] — массив остаётся массивом.
    if len(parts) >= 3 and parts[0] == "financeRec" and parts[1] in FR_LIST_ID_FIELDS:
        list_key = parts[1]
        id_field = FR_LIST_ID_FIELDS[list_key]
        item_id = str(parts[2] or "").strip()
        rest = parts[3:]
        fr_raw = out.get("financeRec")
        items_raw = fr_raw.get(list_key) if isinstance(fr_raw, dict) else None
        # Legacy/test: list_key как dict — не трогаем (общий dict-walk ниже).
        if isinstance(items_raw, dict):
            pass
        elif list_key == "operations" and item_id == FR_OPS_BULK_SEGMENT:
            # Виртуальный путь — в массив не пишем.
            return out
        elif not item_id:
            return out
        else:
            fr = _clone(fr_raw) if isinstance(fr_raw, dict) else {}
            items = _clone(items_raw) if isinstance(items_raw, list) else []
            # value: null на всю запись не материализуем здесь (см. apply_state_op).
            if not rest and value is None:
                return out
            if not rest:
                if not isinstance(value, dict):
                    return out
                cloned = _clone(value)
                cloned[id_field] = item_id
                idx, _row = _list_item_by_id(items, item_id, id_field)
                if idx >= 0:
                    items[idx] = cloned
                else:
                    items.append(cloned)
                fr[list_key] = items
                out["financeRec"] = fr
                return out
            # Поле: только существующая запись — без «скелета» с пустыми полями.
            idx, row = _list_item_by_id(items, item_id, id_field)
            if idx < 0 or not isinstance(row, dict):
                return out
            row = _clone(row)
            cur_row: Any = row
            for part in rest[:-1]:
                nxt = cur_row.get(part) if isinstance(cur_row, dict) else None
                if not isinstance(nxt, dict):
                    nxt = {}
                    if isinstance(cur_row, dict):
                        cur_row[part] = nxt
                cur_row = nxt
            leaf = rest[-1]
            if not isinstance(cur_row, dict):
                return out
            if value is None:
                cur_row.pop(leaf, None)
            else:
                cur_row[leaf] = _clone(value)
            row[id_field] = item_id
            items[idx] = row
            fr[list_key] = items
            out["financeRec"] = fr
            return out
    cur = out
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    leaf = parts[-1]
    if value is None:
        cur.pop(leaf, None)
    else:
        cur[leaf] = _clone(value)
    return out


def records_map(payload: dict | None) -> dict:
    if not isinstance(payload, dict):
        return {}
    raw = payload.get(WT_RECORDS_KEY)
    return raw if isinstance(raw, dict) else {}


def record_version(payload: dict | None, path: str) -> int:
    meta = records_map(payload).get(path)
    if not isinstance(meta, dict):
        return 0
    try:
        return int(meta.get("v") or 0)
    except (TypeError, ValueError):
        return 0


WT_RECORDS_SOFT_MAX = 5000
WT_RECORDS_TARGET = 4000


def prune_record_meta(payload: dict, meta: dict) -> dict:
    """
    Сжимает _wt_records при переполнении.

    Критично: пути, у которых значение ещё есть в payload, НЕ вытесняются.
    Иначе после очистки record_v снова становится 0 и op с expected_v=0
    незаметно перезаписывает чужие данные.

    Удаляются только «сироты» (значение по пути отсутствует). Если живых
    записей больше soft-max — они сохраняются целиком (лимит не режет контроль).
    """
    if not isinstance(meta, dict) or len(meta) <= WT_RECORDS_SOFT_MAX:
        return meta
    live: dict = {}
    orphans: list[tuple[str, dict]] = []
    for path, row in meta.items():
        if not isinstance(row, dict):
            continue
        p = str(path)
        if get_path_value(payload, p) is not None:
            live[p] = row
        else:
            orphans.append((p, row))
    # Живые пути никогда не выбрасываем.
    if len(live) >= WT_RECORDS_SOFT_MAX:
        return live
    orphans_sorted = sorted(
        orphans,
        key=lambda kv: str((kv[1] or {}).get("updated_at") or ""),
    )
    budget = max(0, WT_RECORDS_TARGET - len(live))
    kept = dict(orphans_sorted[-budget:]) if budget else {}
    kept.update(live)
    return kept


def bump_record_meta(payload: dict, path: str, user_id: str, *, exclusive: bool = False) -> dict:
    """
    Поднимает record_v по path.
    exclusive=True — payload уже независимая копия (после set_path_value в OPS);
    полный _clone тогда не нужен. По умолчанию exclusive=False (PUT sync и пр.).
    """
    if not isinstance(payload, dict):
        out: dict = {}
    elif exclusive:
        out = payload
    else:
        out = _clone(payload)
    # Копия карты meta: не делить dict с возможным общим снимком при exclusive=False
    # после _clone это локальная копия; при exclusive — отвязываем meta от прежнего объекта.
    meta = dict(records_map(out))
    prev = record_version(out, path)
    meta[path] = {
        "v": prev + 1,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "updated_by": str(user_id or "")[:120],
    }
    meta = prune_record_meta(out, meta)
    out[WT_RECORDS_KEY] = meta
    return out


def remember_idempotency(payload: dict, key: str, result: dict) -> dict:
    """
    Пишет результат idempotency в payload.
    Вызывается только после OPS с уже независимым next_payload: полный _clone
    всего снимка не нужен — достаточно нового верхнего dict и копии bag.
    """
    if not isinstance(payload, dict):
        out: dict = {}
    else:
        out = dict(payload)
    bag = out.get(WT_IDEMPOTENCY_KEY)
    if not isinstance(bag, dict):
        bag = {}
    else:
        bag = dict(bag)
    bag[str(key)[:160]] = {
        "at": datetime.now(timezone.utc).isoformat(),
        "result": _clone(result),
    }
    if len(bag) > WT_IDEMPOTENCY_MAX:
        ordered = sorted(bag.items(), key=lambda kv: str((kv[1] or {}).get("at") or ""))
        bag = dict(ordered[-WT_IDEMPOTENCY_MAX:])
    out[WT_IDEMPOTENCY_KEY] = bag
    return out


def load_idempotency(payload: dict | None, key: str) -> dict | None:
    if not key or not isinstance(payload, dict):
        return None
    bag = payload.get(WT_IDEMPOTENCY_KEY)
    if not isinstance(bag, dict):
        return None
    row = bag.get(str(key)[:160])
    if not isinstance(row, dict):
        return None
    res = row.get("result")
    return res if isinstance(res, dict) else None


# Вершины JSON, которые могут меняться и через PUT, и через /api/state/op.
# Изменения листьев под ними поднимают record_v, чтобы op видел реальный конфликт.
WT_OPS_TRACKED_TOPS = frozenset(
    {
        "shopMetrics",
        "deptMetrics",
        "shopMetricCatalog",
        "shopPayroll",
        "salary",
        "financeRec",
        "shopSalesPlan",
        "deptSalesPlans",
        "shopRevision",
        "deptRevisions",
        "orgStructure",
        "customerReturns",
        "management",
        "workspaces",
        # Админ/справочники — тоже leaf-op, чтобы не тащить широкий PUT.
        "users",
        "navLayouts",
        "positionCatalog",
        "vacationPlans",
        "fleetVehicles",
        "repSales",
        "shopWriteoffLosses",
    }
)

# Списки сущностей внутри workspace, адресуемые по id (не по индексу массива).
WT_WORKSPACE_ID_LISTS = frozenset({"employees"})


def protect_wt_records_on_put(current: dict, stored: dict, incoming: dict) -> dict:
    """
    Если точечный API уже обновил путь (server_v > client_v), старый PUT
    не должен вернуть устаревший лист даже при совпадении глобальной version.

    Клиентский PUT не может завысить _wt_records: карта версий остаётся серверной.
    Завышенный client_v трактуется как устаревший снимок (лист восстанавливается).
    """
    if not isinstance(stored, dict):
        return stored
    cur_map = records_map(current)
    if not cur_map:
        # Клиентский _wt_records / _wt_idempotency никогда не принимаем как источник истины.
        out = _clone(stored)
        out.pop(WT_RECORDS_KEY, None)
        out.pop(WT_IDEMPOTENCY_KEY, None)
        if WT_RECORDS_KEY in current:
            out[WT_RECORDS_KEY] = _clone(current.get(WT_RECORDS_KEY))
        if WT_IDEMPOTENCY_KEY in current:
            out[WT_IDEMPOTENCY_KEY] = _clone(current.get(WT_IDEMPOTENCY_KEY))
        return out
    in_map = records_map(incoming)
    out = _clone(stored)
    for path, meta in cur_map.items():
        if not isinstance(meta, dict):
            continue
        try:
            server_v = int(meta.get("v") or 0)
        except (TypeError, ValueError):
            server_v = 0
        client_meta = in_map.get(path) if isinstance(in_map.get(path), dict) else {}
        try:
            client_v = int(client_meta.get("v") or 0)
        except (TypeError, ValueError):
            client_v = 0
        # Завышенная клиентская версия не даёт право переписать лист.
        if client_v > server_v:
            client_v = 0
        if server_v > client_v:
            out = set_path_value(out, path, get_path_value(current, path))
    # Карта версий и идемпотентность — только серверные (PUT их не поднимает и не затирает).
    out[WT_RECORDS_KEY] = _clone(cur_map)
    if WT_IDEMPOTENCY_KEY in current:
        out[WT_IDEMPOTENCY_KEY] = _clone(current.get(WT_IDEMPOTENCY_KEY))
    return out


def _is_trackable_op_path(path: str) -> bool:
    norm = normalize_op_path(path)
    if not norm:
        return False
    top = norm.split(".", 1)[0]
    return top in WT_OPS_TRACKED_TOPS


def _collect_changed_op_paths(before: Any, after: Any) -> list[str]:
    """
    Листья (и списки), изменившиеся между снимками, в нотации путей op.
    Независимые пути не смешиваются — каждый лист отдельная запись.
    """
    changed: list[str] = []

    def walk(left: Any, right: Any, path: str) -> None:
        if path:
            top = path.split(".", 1)[0]
            if top in WT_OPS_META_KEYS or top.startswith("_"):
                return
            if top not in WT_OPS_TRACKED_TOPS:
                return
        if _same(left, right):
            return

        if path == "":
            left_d = left if isinstance(left, dict) else {}
            right_d = right if isinstance(right, dict) else {}
            for key in set(left_d) | set(right_d):
                if key in WT_OPS_META_KEYS or str(key).startswith("_"):
                    continue
                if key not in WT_OPS_TRACKED_TOPS:
                    continue
                walk(left_d.get(key), right_d.get(key), str(key))
            return

        if path == "workspaces":
            left_list = left if isinstance(left, list) else []
            right_list = right if isinstance(right, list) else []
            codes: set[str] = set()
            for ws in list(left_list) + list(right_list):
                if isinstance(ws, dict) and ws.get("code") not in (None, ""):
                    codes.add(str(ws.get("code")))
            for code in codes:
                _, lws = _workspace_by_code(left_list, code)
                _, rws = _workspace_by_code(right_list, code)
                walk(lws, rws, f"workspaces.{code}")
            return

        # workspaces.<code>.employees — по id, не целиком список.
        path_parts = path.split(".") if path else []
        if (
            len(path_parts) == 3
            and path_parts[0] == "workspaces"
            and path_parts[2] in WT_WORKSPACE_ID_LISTS
        ):
            left_list = left if isinstance(left, list) else []
            right_list = right if isinstance(right, list) else []
            ids: set[str] = set()
            for row in list(left_list) + list(right_list):
                if isinstance(row, dict) and row.get("id") not in (None, ""):
                    ids.add(str(row.get("id")))
            for eid in ids:
                _, lrow = _list_item_by_id(left_list, eid)
                _, rrow = _list_item_by_id(right_list, eid)
                walk(lrow, rrow, f"{path}.{eid}")
            return

        # users — по user_id.
        if path == "users":
            left_list = left if isinstance(left, list) else []
            right_list = right if isinstance(right, list) else []
            uids: set[str] = set()
            for row in list(left_list) + list(right_list):
                if isinstance(row, dict) and row.get("user_id") not in (None, ""):
                    uids.add(str(row.get("user_id")))
            for uid in uids:
                lrow = next(
                    (
                        r
                        for r in left_list
                        if isinstance(r, dict) and str(r.get("user_id") or "") == uid
                    ),
                    None,
                )
                rrow = next(
                    (
                        r
                        for r in right_list
                        if isinstance(r, dict) and str(r.get("user_id") or "") == uid
                    ),
                    None,
                )
                walk(lrow, rrow, f"users.{uid}")
            return

        # financeRec.{operations|audit|import_batches} — по id-полю, не целиком массив.
        for list_key, id_field in FR_LIST_ID_FIELDS.items():
            if path != f"financeRec.{list_key}":
                continue
            left_list = left if isinstance(left, list) else []
            right_list = right if isinstance(right, list) else []
            # Legacy dict-shape не режем по id здесь.
            if isinstance(left, dict) or isinstance(right, dict):
                if _is_trackable_op_path(path):
                    changed.append(path)
                return
            ids: set[str] = set()
            for row in list(left_list) + list(right_list):
                if isinstance(row, dict) and row.get(id_field) not in (None, ""):
                    ids.add(str(row.get(id_field)))
            for oid in ids:
                if list_key == "operations" and oid == FR_OPS_BULK_SEGMENT:
                    continue
                _, lrow = _list_item_by_id(left_list, oid, id_field)
                _, rrow = _list_item_by_id(right_list, oid, id_field)
                # Гранулярность — запись целиком (так её пишет OPS по id), не отдельные поля.
                if not _same(lrow, rrow):
                    changed.append(f"financeRec.{list_key}.{oid}")
            return

        # financeRec.{openings|day_closings|period_states} — по смысловому ключу строки.
        for list_key in FR_ROW_KEY_LISTS:
            if path != f"financeRec.{list_key}":
                continue
            if isinstance(left, dict) or isinstance(right, dict):
                if _is_trackable_op_path(path):
                    changed.append(path)
                return
            left_list = left if isinstance(left, list) else []
            right_list = right if isinstance(right, list) else []
            keys: set[str] = set()
            for row in list(left_list) + list(right_list):
                k = fr_row_key(list_key, row)
                if k:
                    keys.add(k)
            any_row = False
            for k in keys:
                _, lrow = _fr_row_by_key(left_list, list_key, k)
                _, rrow = _fr_row_by_key(right_list, list_key, k)
                if not _same(lrow, rrow):
                    changed.append(f"financeRec.{list_key}.{k}")
                    any_row = True
            # Изменились только дубли/строки без ключа — версия корня всё равно растёт.
            if not any_row and _is_trackable_op_path(path):
                changed.append(path)
            return

        if isinstance(left, dict) and isinstance(right, dict):
            for key in set(left) | set(right):
                sk = str(key)
                if sk.startswith("_"):
                    continue
                walk(left.get(key), right.get(key), f"{path}.{sk}")
            return

        # Один dict, другой нет / list / scalar — лист для op.
        if isinstance(left, dict) and right is None:
            for key in left:
                sk = str(key)
                if sk.startswith("_"):
                    continue
                walk(left.get(key), None, f"{path}.{sk}")
            return
        if isinstance(right, dict) and left is None:
            for key in right:
                sk = str(key)
                if sk.startswith("_"):
                    continue
                walk(None, right.get(key), f"{path}.{sk}")
            return

        if _is_trackable_op_path(path):
            changed.append(path)

    walk(before, after, "")
    return changed


def sync_record_versions_after_put(current: dict, stored: dict, user: dict | None) -> dict:
    """
    После успешного (с точки зрения protect) PUT поднимает record_v у реально
    изменившихся путей. Иначе последующий op со старым expected_v перезапишет
    изменение PUT незаметно.

    Карта _wt_records остаётся серверной: клиентский payload её не задаёт.
    """
    if not isinstance(stored, dict):
        return stored
    out = stored
    uid = str((user or {}).get("user_id") or "")[:120]
    bumped: set[str] = set()

    def bump_if_changed(path: str) -> None:
        nonlocal out
        if path in bumped or not _is_trackable_op_path(path):
            return
        if _same(get_path_value(current, path), get_path_value(out, path)):
            return
        out = bump_record_meta(out, path, uid)
        bumped.add(path)

    # 1) Уже отслеживаемые пути.
    for path in set(records_map(current)) | set(records_map(out)):
        bump_if_changed(str(path))
    # 2) Новые изменения под tracked tops.
    for path in _collect_changed_op_paths(current, out):
        bump_if_changed(path)
    # 3) Финконтроль: изменённая запись по id → поднять и корень списка.
    for path in list(bumped):
        fr_child = _fr_list_root_of(path)
        if fr_child is not None:
            bump_if_changed(fr_child[0])
    if WT_IDEMPOTENCY_KEY in current and WT_IDEMPOTENCY_KEY not in out:
        out = _clone(out)
        out[WT_IDEMPOTENCY_KEY] = _clone(current.get(WT_IDEMPOTENCY_KEY))
    return out


def _ops_metric_branch_allowed(user: dict, branch: str) -> bool:
    """Права на конкретную ветку метрик, не на весь префикс shopMetrics/deptMetrics.

    Паритет с PUT `_metric_root_forbidden` / `_apply_metric_root`:
    setup → только workAccess/extraAccess; input → только METRIC_INPUT_KEYS.
    """
    if branch in METRIC_ADMIN_META_KEYS:
        return False
    if branch in METRIC_SETUP_KEYS:
        return _can_edit_metric_setup(user)
    if branch in METRIC_INPUT_KEYS:
        return _can_edit_metric_input(user)
    return False


def _ops_metric_access_emp_allowed(
    payload: dict | None, dept_code: str, branch: str, tail: list[str]
) -> bool:
    """Как PUT `_merge_access_map(allowed)`: leaf workAccess/extraAccess только для emp своего отдела.

    Требует путь до empId (целиком месяц/карта — запрет для staff; admin уже early-return).
    workAccess.<month>.<empId> | extraAccess.<empId>
    """
    if branch not in METRIC_SETUP_KEYS:
        return True
    emp_id = ""
    if branch == "workAccess":
        if len(tail) < 2:
            return False
        emp_id = str(tail[1] or "")
    elif branch == "extraAccess":
        if len(tail) < 1:
            return False
        emp_id = str(tail[0] or "")
    if not emp_id:
        return False
    emp_dept = _employee_dept_map(payload if isinstance(payload, dict) else {})
    return emp_dept.get(emp_id) == str(dept_code or "")


# Поля продаж ТП (как в UI setRepSalesField) и проценты terms (как isRepPercentField).
REP_SALES_MONEY_FIELDS = frozenset(
    {
        "totalSales",
        "wholesale",
        "specialPrice",
        "sugar",
        "bags",
        "bagRate",
        "fuel",
        "dayHoldPct",
    }
)
REP_PERCENT_FIELDS = frozenset(
    {
        "salesPercent",
        "salesPercentOpt",
        "salesPercentWholesale",
        "salesPercentSpecial",
    }
)
SALES_REP_POSITION_KEY = "торговый представитель"


def _find_workspace_employee(payload: dict | None, dept_code: str, emp_id: str) -> dict | None:
    if not isinstance(payload, dict) or not dept_code or not emp_id:
        return None
    _idx, ws = _workspace_by_code(payload.get("workspaces"), dept_code)
    if not isinstance(ws, dict):
        return None
    _eidx, row = _list_item_by_id(ws.get("employees"), emp_id)
    return row if isinstance(row, dict) else None


def _employee_is_sales_rep(emp: dict | None) -> bool:
    """Согласовано с клиентским isSalesRep: вкладка sales или должность ТП."""
    if not isinstance(emp, dict):
        return False
    tabs: set[str] = set()
    for key in ("work_tab_id", "primary_work_tab_id", "work_group_id"):
        raw = str(emp.get(key) or "").strip()
        if raw:
            tabs.add(raw)
    for key in ("work_tab_ids", "work_group_ids"):
        for item in emp.get(key) or []:
            raw = str(item or "").strip()
            if raw:
                tabs.add(raw)
    if "sales" in tabs:
        return True
    pos = re.sub(r"\s+", " ", str(emp.get("position") or "").strip().lower())
    return pos == SALES_REP_POSITION_KEY


def _ops_salary_rep_sales_money_allowed(payload: dict | None, path: str, user: dict) -> bool:
    """salary.edit: только repSales.<month>.<empId>.<moneyField> для ТП в drivers."""
    if not user_has_perm(user, "salary", "edit"):
        return False
    parts = [p for p in str(path or "").split(".") if p]
    if len(parts) != 4 or parts[0] != "repSales":
        return False
    if parts[3] not in REP_SALES_MONEY_FIELDS:
        return False
    emp = _find_workspace_employee(payload, "drivers", parts[2])
    return _employee_is_sales_rep(emp)


def _ops_salary_rep_percent_terms_allowed(payload: dict | None, path: str, user: dict) -> bool:
    """
    salary.edit: проценты ТП в карточке drivers —
    workspaces.drivers.employees.<id>.terms.<month>.<percentField>
    или зеркало workspaces.drivers.employees.<id>.<percentField>.
    """
    if not user_has_perm(user, "salary", "edit"):
        return False
    parts = [p for p in str(path or "").split(".") if p]
    if len(parts) < 5 or parts[0] != "workspaces" or parts[1] != "drivers" or parts[2] != "employees":
        return False
    emp_id = parts[3]
    emp = _find_workspace_employee(payload, "drivers", emp_id)
    if not _employee_is_sales_rep(emp):
        return False
    if len(parts) == 5 and parts[4] in REP_PERCENT_FIELDS:
        return True
    if (
        len(parts) == 7
        and parts[4] == "terms"
        and parts[6] in REP_PERCENT_FIELDS
    ):
        return True
    return False


def ops_path_allowed(user: dict | None, path: str, payload: dict | None = None) -> bool:
    if not user or not path:
        return False
    parts = path.split(".")
    top = parts[0]
    if top in WT_OPS_META_KEYS or top.startswith("_"):
        return False
    if is_app_admin(user):
        return True
    codes = user_dept_codes(user)
    if top == "shopMetrics":
        if "shop" not in codes or len(parts) < 2:
            return False
        branch = parts[1]
        if not _ops_metric_branch_allowed(user, branch):
            return False
        return _ops_metric_access_emp_allowed(payload, "shop", branch, parts[2:])
    if top == "deptMetrics":
        if len(parts) < 3 or parts[1] not in codes:
            return False
        branch = parts[2]
        if not _ops_metric_branch_allowed(user, branch):
            return False
        return _ops_metric_access_emp_allowed(payload, parts[1], branch, parts[3:])
    if top == "shopMetricCatalog":
        return _can_edit_metric_setup(user)
    if top == "shopPayroll":
        if "shop" not in codes:
            return False
        has_salary = user_has_perm(user, "salary", "edit")
        has_setup = _can_edit_metric_setup(user)
        if not (has_salary or has_setup):
            return False
        # Денежные KPI — только администратор (как в старом PUT).
        if len(parts) >= 2 and parts[1] in SHOP_PAYROLL_KPI_MONEY_KEYS:
            return False
        # metricsSetup без salary: только связка metrics, не оклады/правила/ручные.
        if not has_salary:
            if len(parts) < 2:
                return False
            return parts[1] == "metrics"
        return True
    if top == "salary":
        # Расчёт — любые поля начислений; выдача — только issued.
        if len(parts) >= 4 and parts[3] == "issued":
            return user_has_salary_pay(user)
        if user_has_perm(user, "salary", "edit"):
            return True
        return False
    if top == "financeRec":
        if len(parts) < 2:
            return False
        branch = parts[1]
        if branch in FINANCE_DATA_KEYS:
            return user_has_perm(user, "financeRec", "edit")
        return user_has_perm(user, "financeRecSetup", "edit")
    if top == "shopSalesPlan":
        return "shop" in codes and user_has_perm(user, "salesPlan", "edit")
    if top == "deptSalesPlans":
        return len(parts) >= 2 and parts[1] in codes and user_has_perm(user, "salesPlan", "edit")
    if top == "shopRevision":
        return "shop" in codes and user_has_perm(user, "revision", "edit")
    if top == "deptRevisions":
        return len(parts) >= 2 and parts[1] in codes and user_has_perm(user, "revision", "edit")
    if top == "orgStructure":
        return user_has_perm(user, "orgStructure", "edit")
    if top == "customerReturns":
        return user_has_perm(user, "customerReturns", "edit")
    if top == "management":
        if len(parts) < 2:
            return _can_write_any_management(user)
        return _can_write_mgmt_key(user, parts[1])
    if top == "workspaces":
        # adjust + salary.edit: только проценты ТП (до проверки departments).
        if _ops_salary_rep_percent_terms_allowed(payload, path, user):
            return True
        if len(parts) < 3 or parts[1] not in codes:
            return False
        if parts[2] == "timesheet":
            return user_has_perm(user, "timesheet", "edit")
        if parts[2] in ("employees", "employeeIds", "removedEmployeeIds"):
            return can_write_dept_employees(user)
        return False
    if top == "shopWriteoffLosses":
        return "shop" in codes and (
            _can_edit_metric_input(user) or _can_edit_metric_setup(user)
        )
    if top == "navLayouts":
        # Единое меню разделов — только системный администратор (не settings:edit).
        return is_app_admin(user)
    if top == "vacationPlans":
        return user_has_perm(user, "vacationPlan", "edit") or user_has_perm(user, "salary", "edit")
    if top == "repSales":
        if _ops_salary_rep_sales_money_allowed(payload, path, user):
            return True
        return user_has_perm(user, "settings", "edit") or can_write_dept_employees(user)
    if top in ("positionCatalog", "fleetVehicles"):
        return user_has_perm(user, "settings", "edit") or can_write_dept_employees(user)
    # users — только admin (ранний return is_app_admin).
    return False


def _ops_closed_salary_value_forbidden(payload: dict, path: str, value: Any, user: dict) -> bool:
    """
    OPS пишет value как есть (без _overlay_open). Без salary нельзя подставить
    закрытые зарплатные поля через workspaces.*.employees (ставка и т.п.).
    Для salary.* — Calc не пишет issued, Pay не пишет начисления.
    """
    parts = [p for p in str(path or "").split(".") if p]
    if parts and parts[0] == "salary":
        if is_app_admin(user):
            return False
        can_calc = user_has_perm(user, "salary", "edit")
        can_pay = user_has_salary_pay(user)
        if len(parts) >= 4:
            return _salary_record_field_forbidden(parts[3], can_calc, can_pay)
        if len(parts) == 3 and isinstance(value, dict):
            current = get_path_value(payload, path)
            return _salary_record_diff_forbidden(current, value, can_calc, can_pay)
        if len(parts) == 2 and isinstance(value, dict):
            current = get_path_value(payload, path)
            cur_month = current if isinstance(current, dict) else {}
            for emp_id, rec in value.items():
                if _salary_record_diff_forbidden(cur_month.get(emp_id), rec, can_calc, can_pay):
                    return True
            return False
        return not can_calc
    if is_app_admin(user) or user_has_perm(user, "salary", "edit"):
        return False
    if len(parts) >= 3 and parts[0] == "workspaces" and parts[2] == "employees":
        current = get_path_value(payload, ".".join(parts[:3]))
        # workspaces.shop.employees.<id>.rate
        if len(parts) >= 5 and parts[4] in SALARY_FIELD_KEYS:
            return True
        if len(parts) == 3 and isinstance(value, list):
            return _dept_employees_closed_salary_write(current, value)
        if len(parts) >= 4 and isinstance(value, dict):
            return _dept_employees_closed_salary_write(current, [value])
    return False


def _dept_employees_closed_salary_write(current: Any, incoming: Any) -> bool:
    """True, если в OPS-value есть отличие по закрытым зарплатным полям."""
    if not isinstance(incoming, list):
        return True
    current_list = current if isinstance(current, list) else []
    by_id: dict[str, dict] = {}
    for row in current_list:
        if isinstance(row, dict) and row.get("id") not in (None, ""):
            by_id[str(row.get("id"))] = row
    for item in incoming:
        if not isinstance(item, dict):
            return True
        emp_id = str(item.get("id") or "").strip()
        existing = by_id.get(emp_id) if emp_id else None
        for key, val in item.items():
            if not _is_closed_key(str(key), False):
                continue
            if existing is None:
                if not _closed_is_blank(val):
                    return True
                continue
            if not _same(val, existing.get(key)):
                return True
    return False


def apply_finance_operations_bulk(
    payload: dict, items_raw: Any, user: dict
) -> tuple[dict | None, dict | None]:
    """
    Спец-операция financeRec.operations.$bulk: upsert списка операций по id.
    value = [ { expected_v, value: {finance_operation_id, …} }, … ].
    Чужие операции не трогает. Дубликаты id в запросе — отказ до записи.
    """
    if not isinstance(items_raw, list):
        return None, {
            "error": "invalid value",
            "message": "Значение $bulk должно быть списком элементов {expected_v, value}.",
        }
    if not items_raw:
        return None, {"error": "invalid value", "message": "Список $bulk пуст."}
    if len(items_raw) > FR_OPS_BULK_MAX_ITEMS:
        return None, {
            "error": "too many ops",
            "message": "Не больше %s операций в $bulk за раз." % FR_OPS_BULK_MAX_ITEMS,
        }

    seen: set[str] = set()
    prepared: list[tuple[str, dict, str, int]] = []
    for i, row in enumerate(items_raw):
        if not isinstance(row, dict):
            return None, {
                "error": "invalid value",
                "message": "Элемент $bulk[%s] должен быть объектом." % i,
            }
        try:
            item_ev = int(row.get("expected_v") if row.get("expected_v") is not None else -1)
        except (TypeError, ValueError):
            return None, {
                "error": "invalid expected_v",
                "message": "expected_v элемента $bulk[%s] должно быть числом." % i,
            }
        if item_ev < 0:
            return None, {
                "error": "invalid expected_v",
                "message": "Укажите expected_v (>= 0) для элемента $bulk[%s]." % i,
            }
        val = row.get("value")
        if not isinstance(val, dict):
            return None, {
                "error": "invalid value",
                "message": "value элемента $bulk[%s] должно быть объектом операции." % i,
            }
        oid = str(val.get("finance_operation_id") or "").strip()
        if not oid or oid == FR_OPS_BULK_SEGMENT:
            return None, {
                "error": "invalid value",
                "message": "Нужен корректный finance_operation_id в $bulk[%s]." % i,
            }
        if oid in seen:
            return None, {
                "error": "duplicate id",
                "message": "Дублирующийся finance_operation_id в $bulk: %s" % oid,
                "path": "financeRec.operations." + oid,
            }
        seen.add(oid)
        op_path = "financeRec.operations." + oid
        server_v = record_version(payload, op_path)
        if item_ev != server_v:
            server_value = get_path_value(payload, op_path)
            return None, {
                "error": "conflict",
                "message": "Запись уже изменена другим пользователем.",
                "path": op_path,
                "server_v": server_v,
                "server_value": ops_public_value(op_path, server_value),
                "your_value": ops_public_value(op_path, val),
                "your_expected_v": item_ev,
            }
        prepared.append((oid, val, op_path, item_ev))

    uid = str(user.get("user_id") or "")
    next_payload = _clone(payload) if isinstance(payload, dict) else {}
    fr_raw = next_payload.get("financeRec")
    fr = _clone(fr_raw) if isinstance(fr_raw, dict) else {}
    items_raw_list = fr.get("operations")
    if isinstance(items_raw_list, dict):
        return None, {
            "error": "invalid path",
            "message": "$bulk поддерживается только для списка операций.",
        }
    items = _clone(items_raw_list) if isinstance(items_raw_list, list) else []

    for oid, val, _op_path, _ev in prepared:
        cloned = _clone(val)
        cloned["finance_operation_id"] = oid
        idx, _row = _list_item_by_id(items, oid, "finance_operation_id")
        if idx >= 0:
            items[idx] = cloned
        else:
            items.append(cloned)

    fr["operations"] = items
    next_payload["financeRec"] = fr
    for oid, _val, op_path, _ev in prepared:
        next_payload = bump_record_meta(next_payload, op_path, uid, exclusive=True)
    # Корень списка: широкий писатель со старой версией получит 409, а не тихий wipe.
    next_payload = bump_record_meta(next_payload, "financeRec.operations", uid, exclusive=True)
    return next_payload, None


def _fr_list_root_of(path: str) -> tuple[str, str] | None:
    """financeRec.<list>.<id>[.…] → (root_path, list_key); иначе None."""
    parts = str(path or "").split(".")
    if len(parts) >= 3 and parts[0] == "financeRec" and (
        parts[1] in FR_LIST_ID_FIELDS or parts[1] in FR_ROW_KEY_LISTS
    ):
        return "financeRec." + parts[1], parts[1]
    return None


def _bump_fr_changed_children(before: dict, after: dict, list_key: str, uid: str) -> dict:
    """Запись корня financeRec.<list>: поднять версии реально изменённых записей по id/ключу."""
    b_fr = before.get("financeRec") if isinstance(before, dict) else None
    a_fr = after.get("financeRec") if isinstance(after, dict) else None
    b_list = b_fr.get(list_key) if isinstance(b_fr, dict) else None
    a_list = a_fr.get(list_key) if isinstance(a_fr, dict) else None
    if not isinstance(b_list, list) and not isinstance(a_list, list):
        return after
    b_list = b_list if isinstance(b_list, list) else []
    a_list = a_list if isinstance(a_list, list) else []
    if list_key in FR_ROW_KEY_LISTS:
        keys: set[str] = set()
        for row in b_list + a_list:
            k = fr_row_key(list_key, row)
            if k:
                keys.add(k)
        out_k = after
        for k in sorted(keys):
            _, brow = _fr_row_by_key(b_list, list_key, k)
            _, arow = _fr_row_by_key(a_list, list_key, k)
            if brow == arow:
                continue
            out_k = bump_record_meta(out_k, "financeRec.%s.%s" % (list_key, k), uid, exclusive=True)
        return out_k
    id_field = FR_LIST_ID_FIELDS[list_key]
    ids: set[str] = set()
    for row in b_list + a_list:
        if isinstance(row, dict) and row.get(id_field) not in (None, ""):
            ids.add(str(row.get(id_field)))
    out = after
    for rid in sorted(ids):
        _, brow = _list_item_by_id(b_list, rid, id_field)
        _, arow = _list_item_by_id(a_list, rid, id_field)
        if brow == arow:
            continue
        out = bump_record_meta(out, "financeRec.%s.%s" % (list_key, rid), uid, exclusive=True)
    return out


def _bulk_result_items(payload: dict, path: str, raw_value: Any) -> list[dict] | None:
    """Для $bulk: версии и значения каждой операции — клиенту для expected_v последующих правок."""
    if path != FR_OPS_BULK_PATH or not isinstance(raw_value, list):
        return None
    out: list[dict] = []
    for row in raw_value:
        val = row.get("value") if isinstance(row, dict) else None
        oid = str((val or {}).get("finance_operation_id") or "").strip() if isinstance(val, dict) else ""
        if not oid:
            continue
        op_path = "financeRec.operations." + oid
        # Без value: клиент уже держит тело; не раздуваем ответ / idempotency-кэш.
        out.append({"path": op_path, "record_v": record_version(payload, op_path)})
    return out


def apply_state_op(payload: dict, op: dict, user: dict) -> tuple[dict | None, dict | None]:
    """
    Применяет одну точечную операцию.
    Возвращает (new_payload, None) или (None, error_body).
    """
    path = normalize_op_path(op.get("path"))
    if not path:
        return None, {"error": "invalid path", "message": "Некорректный путь записи."}
    # workspaces — список: путь workspaces.<code>.… должен указывать на существующий code.
    if path.startswith("workspaces."):
        wparts = path.split(".")
        if len(wparts) < 3:
            return None, {"error": "invalid path", "message": "Некорректный путь записи."}
        idx, _ws = _workspace_by_code(
            payload.get("workspaces") if isinstance(payload, dict) else None,
            wparts[1],
        )
        if idx < 0:
            return None, {"error": "invalid path", "message": "Подразделение не найдено."}
    if not ops_path_allowed(user, path, payload):
        return None, {"error": "forbidden", "message": "Недостаточно прав для этой записи."}
    your_value = op.get("value")
    # Физическое удаление карточки через null — только владелец и без значимой истории.
    op_parts = path.split(".")
    if (
        len(op_parts) == 4
        and op_parts[0] == "workspaces"
        and op_parts[2] == "employees"
        and your_value is None
    ):
        if not is_payload_owner(user):
            return None, {
                "error": "forbidden",
                "message": "Удаление сотрудников доступно только владельцу программы.",
                "path": path,
            }
        rm_msg = employee_removal_block_message(payload, op_parts[3])
        if rm_msg:
            return None, {
                "error": "employee_has_history",
                "message": rm_msg,
                "path": path,
                "reasons": employee_linked_history_labels(payload, op_parts[3]),
            }
    if _ops_closed_salary_value_forbidden(payload, path, your_value, user):
        return None, {"error": "forbidden", "message": "Недостаточно прав для этой записи."}
    try:
        expected_v = int(op.get("expected_v") if op.get("expected_v") is not None else -1)
    except (TypeError, ValueError):
        return None, {"error": "invalid expected_v", "message": "Ожидаемая версия записи должна быть числом."}
    if expected_v < 0:
        return None, {"error": "invalid expected_v", "message": "Укажите expected_v (>= 0)."}
    uid = str(user.get("user_id") or "")

    # A: виртуальный bulk-upsert операций (не поле объекта).
    # Версия проверяется по каждой операции внутри; у самого $bulk meta нет.
    if path == FR_OPS_BULK_PATH:
        applied, bulk_err = apply_finance_operations_bulk(payload, your_value, user)
        if bulk_err is not None:
            return None, bulk_err
        return (applied if applied is not None else payload), None

    server_v = record_version(payload, path)
    server_value = get_path_value(payload, path)
    if expected_v != server_v:
        return None, {
            "error": "conflict",
            "message": "Запись уже изменена другим пользователем.",
            "path": path,
            "server_v": server_v,
            "server_value": ops_public_value(path, server_value),
            "your_value": ops_public_value(path, your_value),
            "your_expected_v": expected_v,
        }

    # financeRec.{operations|audit|import_batches}.<id>: валидация до записи.
    fr_op_parts = path.split(".")
    if (
        len(fr_op_parts) >= 3
        and fr_op_parts[0] == "financeRec"
        and fr_op_parts[1] in FR_LIST_ID_FIELDS
    ):
        list_key = fr_op_parts[1]
        item_id = str(fr_op_parts[2] or "").strip()
        id_field = FR_LIST_ID_FIELDS[list_key]
        if not item_id or (list_key == "operations" and item_id == FR_OPS_BULK_SEGMENT):
            return None, {
                "error": "invalid path",
                "message": "Некорректный идентификатор записи Финконтроля.",
            }
        fr_root = payload.get("financeRec") if isinstance(payload, dict) else None
        items_list = fr_root.get(list_key) if isinstance(fr_root, dict) else None
        if isinstance(items_list, list):
            if your_value is None and len(fr_op_parts) == 3:
                if list_key == "operations":
                    return None, {
                        "error": "invalid value",
                        "message": "Удаление операции через null не поддерживается. Используйте is_deleted.",
                    }
                if list_key == "audit":
                    return None, {
                        "error": "immutable",
                        "message": "Запись журнала нельзя удалить через OPS.",
                    }
                if list_key == "money_transits":
                    return None, {
                        "error": "invalid value",
                        "message": "Удаление транзита через null не поддерживается.",
                    }
                return None, {
                    "error": "invalid value",
                    "message": "Удаление партии импорта через null не поддерживается.",
                }
            if len(fr_op_parts) > 3:
                existing = get_path_value(payload, "financeRec.%s.%s" % (list_key, item_id))
                if not isinstance(existing, dict):
                    return None, {
                        "error": "invalid path",
                        "message": "Запись не найдена.",
                    }
                # Журнал: запрет полевых правок существующей истории.
                if list_key == "audit":
                    return None, {
                        "error": "immutable",
                        "message": "Запись журнала нельзя изменять.",
                    }
            if len(fr_op_parts) == 3 and your_value is not None and not isinstance(your_value, dict):
                return None, {
                    "error": "invalid value",
                    "message": "Значение записи должно быть объектом.",
                }
            # Журнал: только создание; повторная запись того же audit_id — отказ
            # (кроме идентичного повтора — идемпотентно, без дубля и без смены версии).
            if list_key == "audit" and len(fr_op_parts) == 3 and isinstance(your_value, dict):
                body_id = str(your_value.get(id_field) or "").strip()
                if body_id and body_id != item_id:
                    return None, {
                        "error": "invalid value",
                        "message": "audit_id в теле должен совпадать с путём.",
                    }
                existing_aud = get_path_value(payload, "financeRec.audit." + item_id)
                if isinstance(existing_aud, dict):
                    want = _clone(your_value)
                    want[id_field] = item_id
                    if _same(existing_aud, want):
                        return payload, None
                    return None, {
                        "error": "immutable",
                        "message": "Запись журнала уже существует и не может быть перезаписана.",
                        "path": path,
                    }

    # financeRec.{openings|day_closings|period_states}.<ключ>: только строка целиком, без удаления.
    if (
        len(fr_op_parts) >= 3
        and fr_op_parts[0] == "financeRec"
        and fr_op_parts[1] in FR_ROW_KEY_LISTS
    ):
        row_list_key = fr_op_parts[1]
        fr_root_r = payload.get("financeRec") if isinstance(payload, dict) else None
        rows_list = fr_root_r.get(row_list_key) if isinstance(fr_root_r, dict) else None
        if len(fr_op_parts) != 3 or not fr_op_parts[2] or isinstance(rows_list, dict):
            return None, {
                "error": "invalid path",
                "message": "Строку остатка или статуса периода можно записать только целиком по ключу.",
            }
        if your_value is None:
            return None, {
                "error": "invalid value",
                "message": "Удаление строки остатка или статуса периода через null не поддерживается.",
            }
        if not isinstance(your_value, dict):
            return None, {"error": "invalid value", "message": "Значение записи должно быть объектом."}
        if fr_row_key(row_list_key, your_value) != fr_op_parts[2]:
            return None, {
                "error": "invalid value",
                "message": "Ключ строки (объект, неделя или дата, счёт) не совпадает с путём записи.",
            }

    # Журнал целиком (legacy-писатели): только дополнение, без удаления/правки истории.
    if path == "financeRec.audit":
        fr_root_a = payload.get("financeRec") if isinstance(payload, dict) else None
        old_list = fr_root_a.get("audit") if isinstance(fr_root_a, dict) else None
        if isinstance(old_list, list) and old_list:
            if not isinstance(your_value, list):
                return None, {
                    "error": "immutable",
                    "message": "Журнал Финконтроля нельзя заменить не-списком.",
                }
            for old_row in old_list:
                if not isinstance(old_row, dict):
                    continue
                aid = str(old_row.get("audit_id") or "")
                if not aid:
                    continue
                _ni, new_row = _list_item_by_id(your_value, aid, "audit_id")
                if new_row is None or not _same(old_row, new_row):
                    return None, {
                        "error": "immutable",
                        "message": "Нельзя удалить или изменить существующую запись журнала.",
                        "path": path,
                        "audit_id": aid,
                    }

    next_payload = set_path_value(payload, path, your_value)
    # users.* через OPS: как PUT — хешируем новый pin и сохраняем pin_hash, если pin не передан.
    if path == "users" or path.startswith("users."):
        next_payload = hash_pins_in_payload(next_payload, payload)
    # set_path_value уже сделал полный _clone — второй полный clone в bump не нужен.
    next_payload = bump_record_meta(next_payload, path, uid, exclusive=True)
    # Финконтроль: связь версий корня списка и записей по id (без тихого wipe).
    fr_child = _fr_list_root_of(path)
    if fr_child is not None:
        next_payload = bump_record_meta(next_payload, fr_child[0], uid, exclusive=True)
    else:
        fr_parts = path.split(".")
        if len(fr_parts) == 2 and fr_parts[0] == "financeRec" and (
            fr_parts[1] in FR_LIST_ID_FIELDS or fr_parts[1] in FR_ROW_KEY_LISTS
        ):
            next_payload = _bump_fr_changed_children(payload, next_payload, fr_parts[1], uid)
    # График отпускных → once vacationPay в ведомости (без права salary у HR).
    if path.startswith("vacationPlans.") and path.count(".") >= 2:
        parts = path.split(".")
        plan_path = ".".join(parts[:3])
        plan_rec = get_path_value(next_payload, plan_path)
        if isinstance(plan_rec, dict):
            next_payload = apply_vacation_pay_once_from_plan(next_payload, plan_rec, uid)
    return next_payload, None


def _planned_vacation_amount(raw: Any) -> int:
    """Как клиентский plannedVacationAmount: только сумма > 0."""
    if raw is None or raw == "":
        return 0
    if isinstance(raw, bool):
        return 0
    if isinstance(raw, (int, float)):
        try:
            n = int(round(float(raw)))
        except (TypeError, ValueError, OverflowError):
            return 0
        return n if n > 0 else 0
    s = str(raw).replace(" ", "").replace(",", ".")
    try:
        n = int(round(float(s)))
    except (TypeError, ValueError, OverflowError):
        return 0
    return n if n > 0 else 0


def apply_vacation_pay_once_from_plan(payload: dict, plan: dict, user_id: str) -> dict:
    """
    После разрешённой записи vacationPlans: один раз выставить
    salary.<ym>.<emp>.vacationPay = planned_amount, если ещё не начислено.
    Не трогает issued / vacationPaidEarlier / прочие поля.
    """
    if not isinstance(payload, dict) or not isinstance(plan, dict):
        return payload
    if str(plan.get("status") or "") == "declined":
        return payload
    date = str(plan.get("planned_date") or "").strip()
    if len(date) < 7 or date[4] != "-":
        return payload
    ym = date[:7]
    if len(ym) != 7:
        return payload
    amt = _planned_vacation_amount(plan.get("planned_amount"))
    if amt <= 0:
        return payload
    emp_id = str(plan.get("employee_id") or "").strip()
    if not emp_id:
        return payload
    out = payload
    salary = out.get("salary")
    if not isinstance(salary, dict):
        salary = {}
        out["salary"] = salary
    else:
        # отвязать верхний dict salary от возможного общего снимка
        salary = dict(salary)
        out["salary"] = salary
    bucket = salary.get(ym)
    if not isinstance(bucket, dict):
        bucket = {}
    else:
        bucket = dict(bucket)
    salary[ym] = bucket
    key = emp_id if emp_id in bucket else None
    if key is None:
        for cand in bucket:
            if str(cand) == emp_id:
                key = cand
                break
    if key is None:
        key = emp_id
    cur = bucket.get(key)
    if isinstance(cur, dict):
        try:
            existing = float(cur.get("vacationPay") or 0)
        except (TypeError, ValueError):
            existing = 0.0
        if existing > 0:
            return out
        row = dict(cur)
    else:
        row = {}
    row["vacationPay"] = amt
    bucket[key] = row
    pay_path = f"salary.{ym}.{key}.vacationPay"
    out = bump_record_meta(out, pay_path, user_id, exclusive=True)
    return out


def vacation_plan_salary_facts(payload: dict) -> dict | None:
    """
    Узкий срез salary для зрителей графика без salary:view:
    только vacationPay / issued / vacationPaidEarlier по сотрудникам из планов.
    """
    plans = payload.get("vacationPlans") if isinstance(payload, dict) else None
    salary = payload.get("salary") if isinstance(payload, dict) else None
    if not isinstance(plans, dict) or not isinstance(salary, dict):
        return None
    emp_ids: set[str] = set()
    for year_list in plans.values():
        if not isinstance(year_list, list):
            continue
        for rec in year_list:
            if isinstance(rec, dict) and rec.get("employee_id") not in (None, ""):
                emp_ids.add(str(rec.get("employee_id")))
    if not emp_ids:
        return None
    out: dict[str, dict] = {}
    for ym, bucket in salary.items():
        if not isinstance(bucket, dict):
            continue
        slim: dict[str, dict] = {}
        for emp_key, rec in bucket.items():
            if str(emp_key) not in emp_ids or not isinstance(rec, dict):
                continue
            try:
                vp = float(rec.get("vacationPay") or 0)
            except (TypeError, ValueError):
                vp = 0.0
            if vp <= 0 and not rec.get("issued") and not rec.get("vacationPaidEarlier"):
                continue
            cell: dict[str, Any] = {}
            if "vacationPay" in rec:
                cell["vacationPay"] = rec.get("vacationPay")
            if "issued" in rec:
                cell["issued"] = bool(rec.get("issued"))
            if "vacationPaidEarlier" in rec:
                cell["vacationPaidEarlier"] = bool(rec.get("vacationPaidEarlier"))
            if cell:
                slim[str(emp_key)] = cell
        if slim:
            out[str(ym)] = slim
    return out or None


def ops_public_value(path: str, value: Any) -> Any:
    """Ответ OPS не должен отдавать pin/pin_hash (как GET /api/state)."""
    p = str(path or "")
    if p == "users" or p.startswith("users."):
        return strip_secrets(value)
    if p.endswith(".pin") or p.endswith(".password") or ".rolePins" in p:
        return None if p.endswith(".pin") or p.endswith(".password") else strip_secrets(value)
    return value


def handle_post_state_op(environ):
    """POST /api/state/op — точечное сохранение одной записи с версией пути."""
    if not ops_enabled():
        return json_bytes(
            {"error": "ops disabled", "message": "Точечное сохранение временно отключено."},
            "503 Service Unavailable",
        )
    raw = read_body(environ)
    try:
        data = json.loads(raw.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return json_bytes({"error": "invalid json"}, "400 Bad Request")
    if not isinstance(data, dict):
        return json_bytes({"error": "body must be an object"}, "400 Bad Request")
    op = data.get("op")
    if not isinstance(op, dict):
        return json_bytes({"error": "op is required"}, "400 Bad Request")
    idem = str(data.get("idempotency_key") or "").strip()
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
            user = current_user_from_state(environ, payload, user_id)
            if not user:
                conn.rollback()
                return json_bytes({"error": "unauthorized"}, "401 Unauthorized")
            if idem:
                cached = load_idempotency(payload, idem)
                if cached is not None:
                    conn.rollback()
                    return json_bytes(cached, "200 OK")
            next_payload, op_err = apply_state_op(payload, op, user)
            if op_err is not None:
                conn.rollback()
                status = "409 Conflict" if op_err.get("error") in ("conflict", "employee_has_history") else (
                    "403 Forbidden" if op_err.get("error") == "forbidden" else "400 Bad Request"
                )
                return json_bytes(op_err, status)
            expected = int(state.get("version") or 0)
            op_path = normalize_op_path(op.get("path")) or ""
            result_body = {
                "ok": True,
                "path": op_path,
                "record_v": record_version(next_payload, op_path),
                "value": ops_public_value(op_path, get_path_value(next_payload, op_path)),
            }
            bulk_items = _bulk_result_items(next_payload, op_path, op.get("value"))
            if bulk_items is not None:
                result_body["items"] = bulk_items
            if idem:
                next_payload = remember_idempotency(next_payload, idem, result_body)
            row = persist_payload(cur, next_payload, expected, str(user.get("user_id") or ""))
            if not row:
                latest = fetch_state(cur)
                conn.rollback()
                return json_bytes(conflict_body(latest), "409 Conflict")
            conn.commit()
            version, updated_at, updated_by_row = row[0], row[1], row[2]
            result_body["version"] = int(version or 0)
            result_body["updated_at"] = (
                updated_at.isoformat() if hasattr(updated_at, "isoformat") else (updated_at or "")
            )
            result_body["updated_by"] = updated_by_row or ""
            return json_bytes(result_body, "200 OK")
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return db_unavailable()
    finally:
        conn.close()


def apply_state_ops_batch(
    payload: dict, ops: list, user: dict, atomic: bool
) -> tuple[dict | None, list[dict], dict | None]:
    """
    Применяет список точечных операций.
    atomic=True — все или ничего (первый конфликт/ошибка откатывает весь batch в памяти).
    atomic=False — независимые пути: успешные применяются, конфликты собираются в results.
    Возвращает (next_payload|None, results, fatal_error|None).
    """
    if not isinstance(ops, list) or not ops:
        return None, [], {"error": "ops required", "message": "Нужен непустой список ops."}
    if len(ops) > 200:
        return None, [], {"error": "too many ops", "message": "Не больше 200 операций за раз."}
    results: list[dict] = []
    next_payload = payload
    for raw_op in ops:
        if not isinstance(raw_op, dict):
            err = {"error": "invalid op", "message": "Каждая операция должна быть объектом."}
            if atomic:
                return None, [], err
            results.append({"status": "error", **err})
            continue
        applied, op_err = apply_state_op(next_payload, raw_op, user)
        path = normalize_op_path(raw_op.get("path")) or str(raw_op.get("path") or "")
        if op_err is not None:
            row = {"status": op_err.get("error") or "error", "path": path, **op_err}
            if atomic:
                return None, [], op_err
            results.append(row)
            continue
        next_payload = applied if applied is not None else next_payload
        saved_row = {
            "status": "saved",
            "path": path,
            "record_v": record_version(next_payload, path),
            "value": ops_public_value(path, get_path_value(next_payload, path)),
        }
        bulk_items = _bulk_result_items(next_payload, path, raw_op.get("value"))
        if bulk_items is not None:
            saved_row["items"] = bulk_items
        results.append(saved_row)
    if atomic and any(r.get("status") != "saved" for r in results):
        return None, results, {"error": "batch failed", "message": "Пакет не применён."}
    return next_payload, results, None


def handle_post_state_op_batch(environ):
    """
    POST /api/state/op/batch — несколько точечных записей в одной транзакции БД.
    Body: { idempotency_key?, atomic?: bool, ops: [{path, expected_v, value}, ...] }
    atomic=true — связанная операция (например facts+category); иначе независимые пути.
    """
    if not ops_enabled():
        return json_bytes(
            {"error": "ops disabled", "message": "Точечное сохранение временно отключено."},
            "503 Service Unavailable",
        )
    raw = read_body(environ)
    try:
        data = json.loads(raw.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return json_bytes({"error": "invalid json"}, "400 Bad Request")
    if not isinstance(data, dict):
        return json_bytes({"error": "body must be an object"}, "400 Bad Request")
    ops = data.get("ops")
    if not isinstance(ops, list):
        return json_bytes({"error": "ops is required"}, "400 Bad Request")
    atomic = bool(data.get("atomic"))
    idem = str(data.get("idempotency_key") or "").strip()
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
            user = current_user_from_state(environ, payload, user_id)
            if not user:
                conn.rollback()
                return json_bytes({"error": "unauthorized"}, "401 Unauthorized")
            if idem:
                cached = load_idempotency(payload, idem)
                if cached is not None:
                    conn.rollback()
                    return json_bytes(cached, "200 OK")
            next_payload, results, fatal = apply_state_ops_batch(payload, ops, user, atomic)
            if fatal is not None:
                conn.rollback()
                status = "409 Conflict" if fatal.get("error") in ("conflict", "employee_has_history") else (
                    "403 Forbidden" if fatal.get("error") == "forbidden" else "400 Bad Request"
                )
                fatal_out = dict(fatal)
                if results:
                    fatal_out["results"] = results
                return json_bytes(fatal_out, status)
            if next_payload is None:
                conn.rollback()
                return json_bytes({"error": "batch empty", "message": "Нечего сохранять."}, "400 Bad Request")
            saved_any = any(r.get("status") == "saved" for r in results)
            if not saved_any:
                conn.rollback()
                # Все пути с конфликтами/ошибками — 409 если есть conflict, иначе 400.
                has_conflict = any(r.get("status") == "conflict" or r.get("error") == "conflict" for r in results)
                return json_bytes(
                    {"ok": False, "results": results, "message": "Ни одна запись не сохранена."},
                    "409 Conflict" if has_conflict else "400 Bad Request",
                )
            expected = int(state.get("version") or 0)
            result_body: dict[str, Any] = {
                "ok": True,
                "atomic": atomic,
                "results": results,
                # Фактическая version подтверждается после persist; для idempotency заранее expected+1.
                "version": expected + 1,
            }
            if idem:
                next_payload = remember_idempotency(next_payload, idem, result_body)
            row = persist_payload(cur, next_payload, expected, str(user.get("user_id") or ""))
            if not row:
                latest = fetch_state(cur)
                conn.rollback()
                return json_bytes(conflict_body(latest), "409 Conflict")
            conn.commit()
            version, updated_at, updated_by_row = row[0], row[1], row[2]
            result_body["version"] = int(version or 0)
            result_body["updated_at"] = (
                updated_at.isoformat() if hasattr(updated_at, "isoformat") else (updated_at or "")
            )
            result_body["updated_by"] = updated_by_row or ""
            return json_bytes(result_body, "200 OK")
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return db_unavailable()
    finally:
        conn.close()


_FR_PUT_KEEP_ABSENT = object()
FR_PUT_KEYED_LISTS = ("audit", "import_batches", "operations")


def _fr_list_put_plan(
    current: dict, incoming: dict, list_key: str, can_write: bool, as_admin: bool
) -> tuple[Any, str | None]:
    """
    Список Финконтроля через PUT (audit / import_batches / operations): по id, не по индексу,
    без физического удаления. Отсутствие записи в запросе — устаревшая копия, серверная остаётся.
    Новый id добавляется; дубль id с другим содержанием или запись без id — конфликт.
    audit: существующие записи неизменяемы. import_batches / operations: правка существующей
    (в т.ч. is_deleted) — только с актуальной версией записи, иначе конфликт.
    Возвращает (итоговый список | _FR_PUT_KEEP_ABSENT, код ошибки | None).
    """
    in_fr = incoming.get("financeRec")
    if not isinstance(in_fr, dict):
        return _FR_PUT_KEEP_ABSENT, None
    id_field = FR_LIST_ID_FIELDS[list_key]
    cur_fr = current.get("financeRec") if isinstance(current.get("financeRec"), dict) else {}
    cur_list = cur_fr.get(list_key) if isinstance(cur_fr.get(list_key), list) else []
    out = _clone(cur_list)
    in_list = in_fr.get(list_key)
    if not isinstance(in_list, list):
        return (out if list_key in cur_fr else _FR_PUT_KEEP_ABSENT), None
    positions: dict[str, list] = {}
    anon: list = []
    for idx, row in enumerate(cur_list):
        rid = row.get(id_field) if isinstance(row, dict) else None
        if rid in (None, ""):
            anon.append(strip_secrets(row))
        else:
            positions.setdefault(str(rid), []).append(idx)
    seen: dict[str, Any] = {}
    changed = False
    for row in in_list:
        rid = row.get(id_field) if isinstance(row, dict) else None
        if rid in (None, ""):
            if any(_same(row, a) for a in anon):
                continue
            return None, "invalid"
        rid = str(rid)
        if rid in seen:
            if _same(row, seen[rid]):
                continue
            return None, "duplicate"
        seen[rid] = row
        if rid in positions:
            if any(_same(row, strip_secrets(cur_list[i])) for i in positions[rid]):
                continue
            if list_key == "audit" or len(positions[rid]) > 1:
                return None, "immutable"
            path = f"financeRec.{list_key}.{rid}"
            server_v = record_version(current, path)
            if server_v and record_version(incoming, path) != server_v:
                return None, "stale"
            if can_write:
                i = positions[rid][0]
                out[i] = _clone(row) if as_admin else _overlay_open(cur_list[i], row, True)
                changed = True
            continue
        if can_write:
            out.append(_clone(row) if as_admin else _overlay_open({}, row, True))
            changed = True
    if not changed and list_key not in cur_fr:
        return _FR_PUT_KEEP_ABSENT, None
    return out, None


def _fr_lists_put_plans(current: dict, incoming: dict, can_write: bool, as_admin: bool) -> tuple[dict, str | None]:
    plans: dict[str, Any] = {}
    for list_key in FR_PUT_KEYED_LISTS:
        plan, err = _fr_list_put_plan(current, incoming, list_key, can_write, as_admin)
        if err:
            return {}, "fr_conflict:" + list_key
        plans[list_key] = plan
    return plans, None


def _fr_lists_put_apply(stored: Any, plans: dict) -> Any:
    if not isinstance(stored, dict):
        return stored
    for list_key, plan in plans.items():
        if plan is _FR_PUT_KEEP_ABSENT:
            continue
        fr = stored.get("financeRec")
        if not isinstance(fr, dict):
            fr = {}
            stored["financeRec"] = fr
        fr[list_key] = plan
    return stored


def prepare_put_payload(current: dict, incoming: dict, user: dict) -> tuple[dict | None, str | None]:
    """Пишем поверх снимка в БД. Запрещённое поле — 403, без частичной записи."""
    if not isinstance(current, dict):
        current = {}
    if not isinstance(incoming, dict):
        return None, "forbidden"
    # Новые дубли ID блокируем; исторические (уже в current) — нет.
    if employee_id_put_denied(current, incoming):
        return None, "duplicate_employee_id"
    # Удаление карточек: сначала владелец (env), затем значимая история.
    denied_owner = employee_delete_owner_denied(user, incoming)
    if denied_owner:
        return None, denied_owner
    denied_rm = employee_removals_denied(current, incoming)
    if denied_rm:
        return None, denied_rm
    if is_app_admin(user):
        if incoming_admin_pin_duplicate(current, incoming):
            return None, "duplicate_pin"
        fr_plans, fr_err = _fr_lists_put_plans(current, incoming, True, True)
        if fr_err:
            return None, fr_err
        stored = _merge_admin_payload(current, incoming)
        # Старый PUT не должен затирать записи, уже обновлённые точечным API.
        stored = protect_wt_records_on_put(current, stored, incoming)
        stored = _fr_lists_put_apply(stored, fr_plans)
        # Изменения через PUT поднимают record_v — иначе op не увидит конфликт.
        return sync_record_versions_after_put(current, stored, user), None
    if _staff_put_forbidden(current, incoming, user):
        return None, "forbidden"
    fr_plans, fr_err = _fr_lists_put_plans(
        current, incoming, user_has_perm(user, "financeRec", "edit"), False
    )
    if fr_err:
        return None, fr_err
    stored = _apply_staff_writes(current, incoming, user)
    # Staff PUT обычно не шлёт counters — сохраняем серверные после reserve.
    if isinstance(stored, dict) and "employeeIdCounters" not in incoming:
        if isinstance(current.get("employeeIdCounters"), dict):
            stored["employeeIdCounters"] = _clone(current.get("employeeIdCounters"))
    stored = protect_wt_records_on_put(current, stored, incoming)
    stored = _fr_lists_put_apply(stored, fr_plans)
    stored = sync_record_versions_after_put(current, stored, user)
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
        "rules",
        "manual",
        "sharedBudgets",
        "sharedBudget",
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


def _payroll_workspace_view(workspace: dict, user: dict) -> dict:
    """
    Зарплатный срез подразделения без операционного членства:
    карточки, employeeIds и табель для расчёта сумм. Без PIN и ролей входа.
    """
    allow_finance = user_has_perm(user, "financeRec", "view")
    out: dict[str, Any] = {}
    for key in ("id", "code", "name", "type", "defaultHours"):
        if key in workspace:
            out[key] = workspace.get(key)
    emps = workspace.get("employees")
    if isinstance(emps, list):
        out["employees"] = [
            drop_closed_fields(emp, True, allow_finance)
            for emp in emps
            if isinstance(emp, dict)
        ]
    else:
        out["employees"] = []
    ids = workspace.get("employeeIds")
    if isinstance(ids, list) and ids:
        out["employeeIds"] = [x for x in ids]
    else:
        # Ведомость клиента читает employeeIds; без списка — из карточек.
        out["employeeIds"] = [
            str(emp.get("id"))
            for emp in out["employees"]
            if emp.get("id") not in (None, "")
        ]
    ts = workspace.get("timesheet")
    out["timesheet"] = drop_closed_fields(ts, True, allow_finance) if ts is not None else {}
    return out


def filter_workspace(
    workspace: dict,
    allowed_codes: set[str],
    user: dict,
    *,
    payroll_access: bool = False,
) -> dict | None:
    code = str(workspace.get("code") or "")
    member = code in allowed_codes
    if not member:
        if payroll_access and code in PAYROLL_DEPT_CODES:
            return _payroll_workspace_view(workspace, user)
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
            # Зарплатный доступ видит табель для расчёта, без права править раздел «Табель».
            if user_has_perm(user, "timesheet", "view") or allow_salary:
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


def filter_wt_records_meta(payload: dict, filtered: dict, _user: dict | None = None) -> dict:
    """
    Версии _wt_records только для путей внутри уже отфильтрованного payload.
    Не добавляет данные разделов — только meta {v, updated_at} по разрешённым веткам.
    """
    raw = records_map(payload)
    if not raw or not isinstance(filtered, dict):
        return {}
    allowed_tops = {
        str(k)
        for k in filtered.keys()
        if k and str(k) not in WT_OPS_META_KEYS and not str(k).startswith("_")
    }
    ws_codes: set[str] = set()
    for ws in filtered.get("workspaces") or []:
        if isinstance(ws, dict) and ws.get("code") not in (None, ""):
            ws_codes.add(str(ws.get("code")))
    out: dict[str, Any] = {}
    for path, meta in raw.items():
        if not isinstance(meta, dict):
            continue
        norm = normalize_op_path(path) or str(path or "").strip()
        if not norm:
            continue
        parts = norm.split(".")
        top = parts[0]
        if top not in allowed_tops:
            continue
        if top == "workspaces" and (len(parts) < 2 or parts[1] not in ws_codes):
            continue
        try:
            ver = int(meta.get("v") or 0)
        except (TypeError, ValueError):
            ver = 0
        if ver < 0:
            ver = 0
        row: dict[str, Any] = {"v": ver}
        if meta.get("updated_at") not in (None, ""):
            row["updated_at"] = meta.get("updated_at")
        out[norm] = row
    return out


def filter_nav_layouts_for_user(payload: dict, user: dict) -> dict:
    """
    Порядок меню раздела — общее read-only для сотрудников доступных разделов.
    Ключи navLayouts ограничиваются user_login_codes (права вкладок не расширяются).
    """
    raw = payload.get("navLayouts")
    if not isinstance(raw, dict):
        return {}
    allowed = {str(c) for c in user_login_codes(user) if c}
    if not allowed:
        return {}
    out: dict[str, Any] = {}
    for key, layout in raw.items():
        k = str(key or "").strip()
        if not k or k not in allowed:
            continue
        if isinstance(layout, dict):
            out[k] = _clone(layout)
        else:
            out[k] = layout
    return out


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
                kept = filter_workspace(item, codes, user, payroll_access=allow_salary)
                if kept:
                    filtered.append(kept)
        out["workspaces"] = filtered

    # Единый порядок вкладок раздела (admin пишет OPS; сотрудники только читают).
    nav_layouts = filter_nav_layouts_for_user(payload, user)
    if nav_layouts:
        out["navLayouts"] = nav_layouts

    can_metrics = (
        user_has_perm(user, "metricsInput", "view")
        or user_has_perm(user, "metricsSetup", "view")
        or user_has_perm(user, "metrics", "view")
    )
    # Справочник метрик: операционный ввод или зарплатный расчёт KPI.
    if (can_metrics or allow_salary) and "shopMetricCatalog" in payload:
        out["shopMetricCatalog"] = drop_closed_fields(
            payload.get("shopMetricCatalog"), allow_salary, allow_finance
        )

    # Магазин: операционные ветки — только членство; зарплатные — по salary*.
    if "shop" in codes:
        if can_metrics and "shopMetrics" in payload:
            out["shopMetrics"] = drop_closed_fields(payload.get("shopMetrics"), allow_salary, allow_finance)
        if user_has_perm(user, "revision", "view") and "shopRevision" in payload:
            out["shopRevision"] = drop_closed_fields(payload.get("shopRevision"), allow_salary, allow_finance)
        if user_has_perm(user, "salesPlan", "view") and "shopSalesPlan" in payload:
            out["shopSalesPlan"] = drop_closed_fields(payload.get("shopSalesPlan"), allow_salary, allow_finance)
    if allow_salary:
        if "shopPayroll" in payload:
            out["shopPayroll"] = drop_closed_fields(payload.get("shopPayroll"), True, allow_finance)
        if "shopMetrics" not in out and "shopMetrics" in payload:
            out["shopMetrics"] = drop_closed_fields(payload.get("shopMetrics"), True, allow_finance)
        if "shopSalesPlan" not in out and "shopSalesPlan" in payload:
            out["shopSalesPlan"] = drop_closed_fields(payload.get("shopSalesPlan"), True, allow_finance)

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
    if allow_salary:
        # Метрики/планы территории для сумм зарплаты — все payroll-отделы, не только member.
        payroll_territory = PAYROLL_DEPT_CODES & TERRITORY_CODES
        metrics_branch = pick_dept_branch(
            payload.get("deptMetrics"), payroll_territory, True, allow_finance
        )
        if metrics_branch:
            merged = dict(out.get("deptMetrics") or {})
            merged.update(metrics_branch)
            out["deptMetrics"] = merged
        plans_branch = pick_dept_branch(
            payload.get("deptSalesPlans"), payroll_territory, True, allow_finance
        )
        if plans_branch:
            merged = dict(out.get("deptSalesPlans") or {})
            merged.update(plans_branch)
            out["deptSalesPlans"] = merged

    if allow_salary and "salary" in payload:
        out["salary"] = drop_closed_fields(payload.get("salary"), True, allow_finance)
    if allow_salary and "repSales" in payload:
        out["repSales"] = drop_closed_fields(payload.get("repSales"), True, allow_finance)
    # График отпускных: право vacationPlan (view/edit), не только salary.
    # Без salary не открываем полную ветку salary/repSales — только vacationPlans
    # и узкий срез фактов выплаты для колонок графика.
    allow_vacation_plan = user_has_perm(user, "vacationPlan", "view")
    if (allow_salary or allow_vacation_plan) and "vacationPlans" in payload:
        out["vacationPlans"] = drop_closed_fields(
            payload.get("vacationPlans"), allow_salary, allow_finance
        )
    if allow_vacation_plan and not allow_salary and "salary" not in out:
        facts = vacation_plan_salary_facts(payload)
        if facts:
            out["salary"] = facts
    if allow_finance and "financeRec" in payload:
        out["financeRec"] = drop_closed_fields(payload.get("financeRec"), allow_salary, True)
    if user_can_open_management(user) and "management" in payload:
        out["management"] = drop_closed_fields(payload.get("management"), allow_salary, allow_finance)
    if user_has_perm(user, "orgStructure", "view") and "orgStructure" in payload:
        out["orgStructure"] = drop_closed_fields(payload.get("orgStructure"), allow_salary, allow_finance)
    if user_has_perm(user, "customerReturns", "view") and "customerReturns" in payload:
        out["customerReturns"] = drop_closed_fields(payload.get("customerReturns"), allow_salary, allow_finance)
    # Версии точечных путей — только внутри уже разрешённых веток (без чужих данных).
    records = filter_wt_records_meta(payload, out, user)
    if records:
        out[WT_RECORDS_KEY] = records
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
    return json_bytes({"ok": True, "ops": ops_enabled()})


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
    return json_bytes({"user": public_session_user(user)}, extra=extra)


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
    return json_bytes({"user": public_session_user(user)})


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


def handle_get_state_version(environ):
    """Лёгкий poll: только version/updated_at, без полного payload (для 2–50 клиентов)."""
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
    updated_at = state.get("updated_at") or ""
    if hasattr(updated_at, "isoformat"):
        updated_at = updated_at.isoformat()
    return json_bytes(
        {
            "version": int(state.get("version") or 0),
            "updated_at": updated_at or "",
        }
    )


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
    before_records = records_map(current_payload)
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
    if denied == "employee_has_history":
        return json_bytes(
            {
                "error": "employee_has_history",
                "message": (
                    "Нельзя удалить сотрудника: есть связанные данные. "
                    "Измените статус на «Уволен» и снимите галочку «В табель»."
                ),
            },
            "409 Conflict",
        )
    if denied == "employee_delete_forbidden":
        return json_bytes(
            {
                "error": "employee_delete_forbidden",
                "message": "Удаление сотрудников доступно только владельцу программы.",
            },
            "403 Forbidden",
        )
    if denied and denied.startswith("fr_conflict:"):
        list_key = denied.split(":", 1)[1]
        body = conflict_body(state)
        body["path"] = "financeRec." + list_key
        body["message"] = {
            "audit": "Журнал Финконтроля: существующие записи нельзя изменить или удалить.",
            "import_batches": "Партии импорта: конфликт с данными сервера (изменены другим сотрудником или повтор id).",
            "operations": "Операции Финконтроля: конфликт с данными сервера (изменены другим сотрудником или повтор id).",
        }.get(list_key, "Финконтроль: конфликт с данными сервера.") + " Обновите данные с сервера."
        return json_bytes(body, "409 Conflict")
    if denied or stored is None:
        return json_bytes({"error": "forbidden"}, "403 Forbidden")
    # Клиенту нужны только поднятые record_v — иначе следующий PUT со старым
    # _wt_records затирает только что записанный лист через protect_wt_records.
    after_records = records_map(stored)
    record_updates: dict[str, Any] = {}
    for path, meta in after_records.items():
        if not isinstance(meta, dict):
            continue
        prev = before_records.get(path) if isinstance(before_records.get(path), dict) else None
        try:
            new_v = int(meta.get("v") or 0)
        except (TypeError, ValueError):
            new_v = 0
        try:
            old_v = int((prev or {}).get("v") or 0)
        except (TypeError, ValueError):
            old_v = 0
        if new_v != old_v or prev is None:
            record_updates[str(path)] = {
                "v": new_v,
                "updated_at": meta.get("updated_at") or "",
                "updated_by": meta.get("updated_by") or "",
            }
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
                catalog_applied_ids = _metric_catalog_applied_ids(
                    current_payload, stored, payload if isinstance(payload, dict) else {}
                )
                # Полный payload не отдаём; record_updates — чтобы клиент не слал устаревший expected.
                # catalog_applied_ids — узкое подтверждение сохранения метрик без GET всего state.
                return json_bytes({
                    "ok": True,
                    "version": int(version or 0),
                    "updated_at": updated_at.isoformat() if hasattr(updated_at, "isoformat") else (updated_at or ""),
                    "updated_by": updated_by_row or "",
                    "record_updates": record_updates,
                    "catalog_applied_ids": catalog_applied_ids,
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
            # Аутсорсинг: карточка есть, в табель (employeeIds) не ставим.
            if (
                employee.get("outsourcing") is not True
                and employee.get("jobStatus") != "fired"
                and eid not in [str(x) for x in ws["employeeIds"]]
            ):
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
        elif method in ("GET", "HEAD") and path == "/api/state/version":
            status, headers, body = handle_get_state_version(environ)
            if method == "HEAD":
                body = b""
        elif method == "PUT" and path == "/api/state":
            status, headers, body = handle_put_state(environ)
        elif method == "POST" and path == "/api/state/op":
            status, headers, body = handle_post_state_op(environ)
        elif method == "POST" and path == "/api/state/op/batch":
            status, headers, body = handle_post_state_op_batch(environ)
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
    # WT_LOCAL_TEST=1: проверка хоста/порта/БД/пользователя и WT_BIND до listen и SQL.
    try:
        from wt_local_guard import (
            LocalTestGuardError,
            enforce_local_test_guard,
            local_test_mode_enabled,
        )

        if local_test_mode_enabled():
            try:
                meta = enforce_local_test_guard()
            except LocalTestGuardError as err:
                print(f"ABORT (local test guard): {err}", flush=True)
                raise SystemExit(2) from err
            print(
                "Local test mode ON: "
                f"db={meta.get('database')}@{meta.get('host')}:{meta.get('port')} "
                f"user={meta.get('user')} bind={meta.get('bind')} "
                "(password hidden)",
                flush=True,
            )
    except ImportError:
        pass
    if args[:1] == ["bootstrap"]:
        raise SystemExit(run_bootstrap_cli())
    host = bind_host()
    port = bind_port()
    print(f"WorkTime store API http://{host}:{port}/api/health (UI not connected)", flush=True)

    class ThreadingWSGIServer(ThreadingMixIn, WSGIServer):
        """Параллельные клиенты (2–50): иначе backlog однопоточного wsgiref даёт connection refused."""

        daemon_threads = True
        allow_reuse_address = True

    httpd = make_server(host, port, application, server_class=ThreadingWSGIServer)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
