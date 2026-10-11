-- Единый снимок appData (тот же JSON, что в localStorage worktime_three_tables_v1).
-- Код приложения при обновлении этот файл не перезаписывает: данные живут в PostgreSQL.

CREATE TABLE IF NOT EXISTS app_state (
    id          SMALLINT PRIMARY KEY CHECK (id = 1),
    payload     JSONB NOT NULL DEFAULT '{}'::jsonb,
    version     INTEGER NOT NULL DEFAULT 0,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by  TEXT NOT NULL DEFAULT ''
);

INSERT INTO app_state (id, payload, version)
VALUES (1, '{}'::jsonb, 0)
ON CONFLICT (id) DO NOTHING;
