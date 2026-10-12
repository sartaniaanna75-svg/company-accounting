#!/usr/bin/env python3
"""Хранилище appData и серверный вход по существующим appData.users. Интерфейс пока не подключается."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
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


def pin_pepper() -> bytes:
    return _env("WT_PIN_PEPPER").encode("utf-8")


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

    conn = psycopg.connect(dsn(), autocommit=False)
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


def prepare_put_payload(current: dict, incoming: dict, user: dict) -> tuple[dict | None, str | None]:
    """Не-админ не подменяет users/PIN/роли и закрытые разделы. Админ пишет снимок целиком."""
    if is_app_admin(user):
        return hash_pins_in_payload(json.loads(json.dumps(incoming)), current), None
    stored = json.loads(json.dumps(incoming))
    if "users" in incoming and incoming_users_forbidden(incoming.get("users"), current.get("users")):
        return None, "forbidden"
    stored["users"] = json.loads(json.dumps(current.get("users") or []))
    for key, perm in (("salary", "salary"), ("financeRec", "financeRec")):
        current_val = current.get(key)
        if user_has_perm(user, perm, "edit"):
            continue
        if key in incoming and json_canon(incoming.get(key)) != json_canon(current_val):
            return None, "forbidden"
        if key in current:
            stored[key] = json.loads(json.dumps(current_val))
        else:
            stored.pop(key, None)
    return stored, None


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


def handle_health():
    if not db_configured():
        return json_bytes({"ok": False, "db": False, "error": "database env is not set"}, "503 Service Unavailable")
    try:
        conn = connect()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        finally:
            conn.close()
        return json_bytes({"ok": True, "db": True})
    except Exception:
        return json_bytes({"ok": False, "db": False, "error": "database unavailable"}, "503 Service Unavailable")


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
    if not can_read_full_state(user):
        return json_bytes({"error": "forbidden"}, "403 Forbidden")
    safe = dict(state)
    safe["payload"] = strip_secrets(payload)
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
    if not can_write_full_state(user):
        return json_bytes({"error": "forbidden"}, "403 Forbidden")
    stored, denied = prepare_put_payload(current_payload, payload, user)
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
                result["payload"] = payload_for_client(user, result.get("payload") or {})
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
        if method == "GET" and path == "/api/health":
            status, headers, body = handle_health()
        elif method == "POST" and path == "/api/login":
            status, headers, body = handle_login(environ)
        elif method == "POST" and path == "/api/logout":
            status, headers, body = handle_logout(environ)
        elif method == "GET" and path == "/api/me":
            status, headers, body = handle_me(environ)
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


def main() -> None:
    host = bind_host()
    port = bind_port()
    print(f"WorkTime store API http://{host}:{port}/api/health (UI not connected)", flush=True)
    httpd = make_server(host, port, application)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
