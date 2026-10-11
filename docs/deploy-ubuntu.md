# WorkTime — развёртывание на Ubuntu 24.04 (ЭТАП 2)

Рабочий JSON `appData` хранится в PostgreSQL, отдельно от кода. Обновление из GitHub не затирает базу.

На этом шаге интерфейс (`app.html`) к API **не подключён**. API слушает только `127.0.0.1`. В интернет сервис пока не выставлять.

## Что ставится на сервер (позже, не сейчас)

- Ubuntu 24.04
- PostgreSQL 16 (пакеты дистрибутива)
- Python 3.12, venv
- nginx — только статику `index.html` / `app.html`, API пока с localhost

## База

Создать роль и БД (пароль только в `/etc/worktime.env`, не в git):

```bash
sudo -u postgres psql -c "CREATE USER worktime WITH PASSWORD '…';"
sudo -u postgres psql -c "CREATE DATABASE worktime OWNER worktime;"
sudo -u postgres psql -d worktime -f /opt/worktime/server/schema.sql
```

## Код

```bash
sudo mkdir -p /opt/worktime
sudo git clone <репозиторий> /opt/worktime
cd /opt/worktime
python3 -m venv .venv
.venv/bin/pip install -r server/requirements.txt
sudo cp .env.example /etc/worktime.env
sudo chmod 600 /etc/worktime.env
```

В `/etc/worktime.env` задать `DATABASE_URL` или `PGHOST` / `PGPORT` / `PGDATABASE` / `PGUSER` / `PGPASSWORD`.

```bash
sudo cp /opt/worktime/server/worktime-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now worktime-api
curl -sS http://127.0.0.1:8780/api/health
```

Ожидается: `{"ok": true, "db": true}`.

## Обновление кода без потери данных

```bash
cd /opt/worktime
git pull
sudo systemctl restart worktime-api
```

Не копировать `.env` в git. Не выполнять `DROP DATABASE`. Резервная копия: `pg_dump worktime`.

## API (для следующего шага)

| Метод | Путь | Назначение |
|-------|------|------------|
| GET | `/api/health` | проверка процесса и PostgreSQL |
| GET | `/api/state` | снимок `{ version, payload, updated_at, updated_by }` |
| PUT | `/api/state` | запись `{ version, payload, updated_by }` |

PUT с устаревшим `version` отвечает **409**: другой сотрудник уже сохранил снимок. Клиент должен перечитать GET и не затирать чужие изменения.
