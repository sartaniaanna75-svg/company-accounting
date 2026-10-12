FROM python:3.12-slim

WORKDIR /app

COPY server/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
    && apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY . /app
ENV WT_STATIC_ROOT=/app
WORKDIR /app/server

EXPOSE 80
# Проверка с 127.0.0.1 (IPv4), не localhost: в slim иначе часто остаётся Health status: starting.
HEALTHCHECK --interval=5s --timeout=3s --start-period=20s --retries=12 \
    CMD curl -fsS http://127.0.0.1:80/api/health || exit 1
CMD ["gunicorn", "--bind", "0.0.0.0:80", "--workers", "2", "--timeout", "60", "wt_api:application"]
