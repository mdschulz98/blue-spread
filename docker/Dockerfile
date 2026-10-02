# syntax=docker/dockerfile:1

# ---- build: resolve dependencies from the lockfile into /app/.venv -----------------------------
FROM python:3.13-slim-bookworm AS build

COPY --from=ghcr.io/astral-sh/uv:0.12.22 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-dev --no-install-project

# ---- runtime: slim image with only the virtualenv and the application code --------------------
FROM python:3.13-slim-bookworm AS runtime

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app --no-create-home app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    WEB_CONCURRENCY=2 \
    FORWARDED_ALLOW_IPS="*"

WORKDIR /app
COPY --from=build --chown=app:app /app/.venv /app/.venv
COPY --chown=app:app alembic.ini ./
COPY --chown=app:app migrations ./migrations
COPY --chown=app:app app ./app
COPY --chown=app:app --chmod=755 docker/start.sh /usr/local/bin/start-web

USER app
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --start-interval=1s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"]

CMD ["start-web"]
