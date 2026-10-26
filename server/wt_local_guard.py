# -*- coding: utf-8 -*-
"""
Защита локального тестового запуска WorkTime (WT_LOCAL_TEST=1).

До любого SQL проверяет DATABASE_URL и WT_BIND.
На Timeweb флаг не задаётся — этот модуль не вмешивается.
Пароли и полные DSN в сообщениях об ошибках не показываются.
"""

from __future__ import annotations

import os
from urllib.parse import unquote, urlparse

LOCAL_TEST_HOSTS = frozenset({"127.0.0.1", "localhost"})
LOCAL_TEST_PORT = 5433
LOCAL_TEST_DB = "worktime_test"
LOCAL_TEST_USER = "worktime_tester"
FORBIDDEN_MARKERS = (
    "timeweb",
    "twcstorage",
    "amazonaws",
    "azure",
    "supabase",
    "neon.tech",
    "railway",
    "render.com",
    "digitalocean",
)
# Обход через отдельные PG*-переменные при локальном тесте запрещён.
PG_BYPASS_VARS = (
    "PGHOST",
    "PGPORT",
    "PGDATABASE",
    "PGUSER",
    "PGPASSWORD",
    "PGSERVICE",
    "PGSERVICEFILE",
    "PGPASSFILE",
)


class LocalTestGuardError(RuntimeError):
    """Локальный тестовый режим: параметры подключения недопустимы."""


def local_test_mode_enabled(environ: dict | None = None) -> bool:
    src = environ if environ is not None else os.environ
    flag = str(src.get("WT_LOCAL_TEST", "") or "").strip().lower()
    return flag in ("1", "true", "yes", "on")


def _env(name: str, environ: dict | None = None, default: str = "") -> str:
    src = environ if environ is not None else os.environ
    return str(src.get(name, default) or default).strip()


def assert_safe_local_database_url(url: str | None) -> dict:
    """
    Разбирает и проверяет DATABASE_URL для локального теста.
    Возвращает словарь {host, port, database, user} без пароля.
    """
    raw = (url or "").strip()
    if not raw:
        raise LocalTestGuardError(
            "WT_LOCAL_TEST=1: задайте DATABASE_URL "
            "(postgresql://worktime_tester:***@127.0.0.1:5433/worktime_test)."
        )
    lowered = raw.lower()
    for bad in FORBIDDEN_MARKERS:
        if bad in lowered:
            raise LocalTestGuardError(
                f"WT_LOCAL_TEST=1: запрещённый маркер в DATABASE_URL («{bad}»). "
                "Подключение не выполняется."
            )
    parsed = urlparse(raw)
    if parsed.scheme not in ("postgresql", "postgres"):
        raise LocalTestGuardError(
            "WT_LOCAL_TEST=1: DATABASE_URL должен начинаться с postgresql://."
        )
    host = (parsed.hostname or "").lower()
    if host not in LOCAL_TEST_HOSTS:
        raise LocalTestGuardError(
            "WT_LOCAL_TEST=1: хост БД должен быть 127.0.0.1 или localhost."
        )
    port = parsed.port if parsed.port is not None else 5432
    if int(port) != LOCAL_TEST_PORT:
        raise LocalTestGuardError(
            f"WT_LOCAL_TEST=1: порт БД должен быть {LOCAL_TEST_PORT}."
        )
    database = unquote((parsed.path or "").lstrip("/").split("/")[0] or "")
    if database != LOCAL_TEST_DB:
        raise LocalTestGuardError(
            f"WT_LOCAL_TEST=1: имя базы должно быть {LOCAL_TEST_DB}."
        )
    user = unquote(parsed.username or "")
    if user != LOCAL_TEST_USER:
        raise LocalTestGuardError(
            f"WT_LOCAL_TEST=1: пользователь БД должен быть {LOCAL_TEST_USER}."
        )
    if not parsed.password:
        raise LocalTestGuardError(
            "WT_LOCAL_TEST=1: в DATABASE_URL должен быть пароль пользователя БД."
        )
    return {
        "host": host,
        "port": int(port),
        "database": database,
        "user": user,
    }


def assert_safe_local_bind(bind: str | None) -> str:
    host = (bind or "").strip().lower() or "127.0.0.1"
    if host not in LOCAL_TEST_HOSTS:
        raise LocalTestGuardError(
            "WT_LOCAL_TEST=1: WT_BIND должен быть 127.0.0.1 или localhost "
            "(не 0.0.0.0 и не внешний адрес)."
        )
    return host


def assert_no_pg_bypass_vars(environ: dict | None = None) -> None:
    src = environ if environ is not None else os.environ
    present = [name for name in PG_BYPASS_VARS if str(src.get(name) or "").strip()]
    if present:
        raise LocalTestGuardError(
            "WT_LOCAL_TEST=1: переменные "
            + ", ".join(present)
            + " запрещены. Укажите только DATABASE_URL (без обхода через PG*)."
        )


def enforce_local_test_guard(environ: dict | None = None) -> dict:
    """
    Полная проверка локального режима. Вызывать до connect().
    Если WT_LOCAL_TEST выключен — no-op, возвращает {}.
    """
    if not local_test_mode_enabled(environ):
        return {}
    assert_no_pg_bypass_vars(environ)
    meta = assert_safe_local_database_url(_env("DATABASE_URL", environ))
    bind = assert_safe_local_bind(_env("WT_BIND", environ, "127.0.0.1"))
    meta["bind"] = bind
    return meta
