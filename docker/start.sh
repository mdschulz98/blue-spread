#!/bin/sh
# Production web entrypoint. Migrations are run by the separate one-shot `migrate` service,
# never here, so the web service can be scaled to multiple replicas safely.
set -eu

exec uvicorn app.main:create_app --factory \
    --host 0.0.0.0 \
    --port 8000 \
    --workers "${WEB_CONCURRENCY:-2}" \
    --proxy-headers \
    --forwarded-allow-ips "${FORWARDED_ALLOW_IPS:-*}" \
    --no-server-header
