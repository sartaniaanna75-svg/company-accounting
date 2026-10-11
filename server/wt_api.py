#!/usr/bin/env python3
"""Минимальное хранилище appData в PostgreSQL. Интерфейс WorkTime пока не подключается."""

from __future__ import annotations

import hmac
import json
import os
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


def api_token() -> str:
    return _env("WT_API_TOKEN")


def static_root() -> Path:
    return Path(_env("WT_STATIC_ROOT") or str(REPO_ROOT)).resolve()


def authorize_state(environ):
    expected = api_token()
    if not expected:
        return json_bytes({"error": "unauthorized"}, "401 Unauthorized")
    auth = str(environ.get("HTTP_AUTHORIZATION") or "")
    prefix = "bearer "
    got = auth[len(prefix):].strip() if auth.lower().startswith(prefix) else ""
    if not got or len(got) != len(expected) or not hmac.compare_digest(got, expected):
        return json_bytes({"error": "unauthorized"}, "401 Unauthorized")
    return None


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


def handle_get_state():
    if not db_configured():
        return db_unavailable()
    try:
        conn = connect()
    except Exception:
        return db_unavailable()
    try:
        with conn.cursor() as cur:
            state = fetch_state(cur)
        conn.commit()
        return json_bytes(state)
    except Exception:
        return db_unavailable()
    finally:
        conn.close()


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
    updated_by = str(data.get("updated_by") or "")[:120]

    if not db_configured():
        return db_unavailable()
    try:
        conn = connect()
    except Exception:
        return db_unavailable()
    try:
        with conn.cursor() as cur:
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
                (json.dumps(payload, ensure_ascii=False), updated_by, STATE_ID, expected),
            )
            row = cur.fetchone()
            if row:
                conn.commit()
                return json_bytes(row_to_state(row))
            state = fetch_state(cur)
            conn.rollback()
            return json_bytes(
                {
                    "error": "conflict",
                    "message": "Данные изменил другой сотрудник. Обновите снимок.",
                    "version": state["version"],
                    "payload": state["payload"],
                    "updated_at": state["updated_at"],
                    "updated_by": state["updated_by"],
                },
                "409 Conflict",
            )
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
        elif method == "GET" and path == "/api/state":
            denied = authorize_state(environ)
            status, headers, body = denied if denied else handle_get_state()
        elif method == "PUT" and path == "/api/state":
            denied = authorize_state(environ)
            status, headers, body = denied if denied else handle_put_state(environ)
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
