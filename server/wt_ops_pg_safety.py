# -*- coding: utf-8 -*-
"""
Защита интеграционных PG-тестов от случайного подключения к рабочей БД.

Разрешён только локальный тестовый DSN:
  host ∈ {127.0.0.1, localhost}
  database == worktime_test
  без SSL-режима, указывающего на удалённый хост

При нарушении — исключение ДО любого SQL.
"""

from __future__ import annotations

from urllib.parse import unquote, urlparse


class UnsafeTestDatabaseError(RuntimeError):
    """WT_TEST_DATABASE_URL не соответствует локальной тестовой среде."""


def assert_safe_test_database_url(url: str | None) -> str:
    raw = (url or "").strip()
    if not raw:
        raise UnsafeTestDatabaseError(
            "WT_TEST_DATABASE_URL не задан. Тесты остановлены без SQL."
        )
    # Запрет очевидных рабочих/удалённых маркеров в сырой строке.
    lowered = raw.lower()
    for bad in (
        "timeweb",
        "twcstorage",
        "amazonaws",
        "azure",
        "supabase",
        "neon.tech",
        "railway",
        "render.com",
        "digitalocean",
    ):
        if bad in lowered:
            raise UnsafeTestDatabaseError(
                f"WT_TEST_DATABASE_URL содержит запрещённый маркер «{bad}». "
                "Подключение запрещено."
            )

    parsed = urlparse(raw)
    if parsed.scheme not in ("postgresql", "postgres"):
        raise UnsafeTestDatabaseError(
            "WT_TEST_DATABASE_URL: схема должна быть postgresql:// или postgres://"
        )

    host = (parsed.hostname or "").lower()
    if host not in ("127.0.0.1", "localhost"):
        raise UnsafeTestDatabaseError(
            f"WT_TEST_DATABASE_URL: хост должен быть 127.0.0.1 или localhost, получено «{host or '—'}»."
        )

    # path: /worktime_test
    db = unquote((parsed.path or "").lstrip("/").split("/")[0] or "")
    if db != "worktime_test":
        raise UnsafeTestDatabaseError(
            f"WT_TEST_DATABASE_URL: имя базы должно быть worktime_test, получено «{db or '—'}»."
        )

    if not parsed.username:
        raise UnsafeTestDatabaseError(
            "WT_TEST_DATABASE_URL: укажите пользователя БД (например worktime_test)."
        )

    # Порт по умолчанию 5432 — ок; нестандартный локальный порт тоже ок.
    return raw
