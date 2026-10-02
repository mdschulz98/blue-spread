# Notes API

Backend REST API for a note-taking service shared by small teams. Notes are Markdown documents
that belong to a team. Members read or edit them according to their role, and concurrent edits
are merged automatically (three-way merge) instead of silently overwriting each other.

**Stack:** Python 3.13 · FastAPI · SQLAlchemy 2.x (async, asyncpg) · Pydantic v2 · Alembic ·
PostgreSQL 17 · PyJWT + pwdlib/argon2 · merge3 · Traefik v3 · uv.

---

## Contents

- [Quick start](#quick-start)
- [Developer tasks](#developer-tasks)
- [Tests and code quality](#tests-and-code-quality)
- [Database migrations](#database-migrations)
- [Deploying to production](#deploying-to-production)
- [Permission model](#permission-model)
- [Concurrent edits: versioning and merging](#concurrent-edits-versioning-and-merging)
- [API overview](#api-overview)
- [Configuration](#configuration)
- [Project layout](#project-layout)

---

## Quick start

Prerequisites: **Docker** (Docker Desktop on Windows/macOS) and **[uv](https://docs.astral.sh/uv/)**.
Every command below works the same in PowerShell, bash and zsh. You don't need `make` or a
locally installed Python, because uv downloads Python 3.13 itself.

```sh
uv sync                          # create .venv with all dependencies (incl. dev tools)
uv run poe init-env              # .env from .env.example, with generated secrets
uv run poe up                    # build + start traefik, db, migrate (one-shot) and web
uv run poe create-superuser      # interactive; or pass --email/--display-name/--password
```

Then open **<http://api.localhost/docs>** (Swagger UI). Use **Authorize** with your superuser's
email as `username`. The Traefik dashboard is at <http://traefik.localhost>.

> `*.localhost` resolves to `127.0.0.1` in browsers and in Windows' `curl.exe`. Some other CLI
> tools don't resolve it; use `curl --resolve api.localhost:80:127.0.0.1 …` or add
> `127.0.0.1 api.localhost traefik.localhost` to your hosts file.

The dev stack bind-mounts `app/` and runs uvicorn with `--reload`, so code changes apply
immediately. File-watch polling is enabled so this also works on Windows/macOS bind mounts.
Postgres is published on `127.0.0.1:${POSTGRES_HOST_PORT:-5432}` for local DB tools.

## Developer tasks

Tasks are defined in `pyproject.toml` ([poethepoet](https://poethepoet.natn.io/)) and run with
`uv run poe <task>`. `uv run poe --help` lists them.

| Task | What it does |
| --- | --- |
| `init-env` | Create `.env` from `.env.example` with generated `JWT_SECRET`/`POSTGRES_PASSWORD` |
| `up` / `down` | Build and start / stop the dev stack |
| `logs [service…]` | Follow container logs |
| `migrate` | Run `alembic upgrade head` in the compose stack |
| `revision -m "msg"` | Autogenerate a migration against the dev database |
| `create-superuser` | `python -m app.cli create-superuser` inside the running `web` container |
| `test` | Run the test suite (needs Docker; Postgres runs via testcontainers) |
| `lint` | `ruff check`, `ruff format --check`, `mypy` |
| `format` | `ruff format` + `ruff check --fix` |
| `check` | `lint` + `test`, which is what CI runs |
| `prod-up` | `docker compose -f compose.yaml -f compose.prod.yaml up -d --build` |

## Tests and code quality

```sh
uv run poe test        # or: uv run pytest -k merge -x
uv run poe lint
```

- Tests run against a **real PostgreSQL 17** started by testcontainers (no SQLite). Alembic
  migrations run once per session, and every API test gets a clean database (tables are
  truncated afterwards).
- `tests/test_merge.py` unit-tests the pure merge logic. `tests/test_note_concurrency.py`
  exercises the same contract over HTTP, including truly concurrent PATCHes.
- `tests/test_migrations.py` fails if the models and the migrations drift apart.
- mypy runs in `strict` mode on `app/`. CI (`.github/workflows/ci.yml`) runs lint, type-check,
  tests, an image build and compose validation on every push and pull request.

## Database migrations

The schema is created and changed **only** by Alembic (`migrations/`). The application never
calls `create_all()`.

```sh
# 1. change models in app/models/
# 2. with the dev stack running (db is published on 127.0.0.1):
uv run poe revision -m "add note pinning"
# 3. review migrations/versions/<new file>, then apply:
uv run poe migrate
```

In every environment, the `migrate` service runs `alembic upgrade head` once and exits.
`web` starts only after it succeeds and never migrates on its own startup, so `web` can be
scaled (`docker compose up -d --scale web=3`) safely.

## Deploying to production

1. Point a DNS record at the host and open ports 80 and 443.
2. Create `.env` (`uv run poe init-env`, or copy `.env.example`) and set at least:
   `ENVIRONMENT=prod`, a strong `JWT_SECRET` (≥ 32 random chars; the app refuses to start
   otherwise), `POSTGRES_PASSWORD`, `API_DOMAIN`, `ACME_EMAIL`, and optionally `WEB_CONCURRENCY`
   and `CORS_ORIGINS`.
3. Start the stack:

   ```sh
   docker compose -f compose.yaml -f compose.prod.yaml up -d --build
   docker compose -f compose.yaml -f compose.prod.yaml exec web python -m app.cli create-superuser
   ```

Production differences from the base stack:

- **TLS:** Let's Encrypt via the TLS-ALPN challenge, with certificates in the `letsencrypt` volume.
- **Redirects:** all HTTP requests are redirected to HTTPS.
- **Headers:** a security-headers middleware sets HSTS, `X-Content-Type-Options: nosniff`,
  `Referrer-Policy: no-referrer` and `X-Frame-Options: DENY`.
- **Traefik dashboard:** disabled.
- **Registration:** off unless `ALLOW_REGISTRATION=true`.
- **Restarts:** `restart: unless-stopped` on all long-running services.

Network layout:

- Only Traefik publishes host ports.
- `db` sits on the internal `backend` network only.
- `web` joins both `proxy` and `backend`.
- The Docker socket is mounted read-only into Traefik.
- Uvicorn trusts `X-Forwarded-*` headers (`--proxy-headers`). `FORWARDED_ALLOW_IPS` defaults
  to `*`, because `web` is reachable only through Traefik.

## Permission model

| | viewer | editor | admin | superuser |
| --- | :-: | :-: | :-: | :-: |
| See the team, its members and notes | ✓ | ✓ | ✓ | ✓ (all teams) |
| Create / edit / soft-delete / restore notes | | ✓ | ✓ | ✓ |
| See deleted notes (`include_deleted`) | | ✓ | ✓ | ✓ |
| Rename / delete the team, manage members and roles | | | ✓ | ✓ |
| Create users (`POST /users`) | | | | ✓ |

- **Visibility:** users see only the teams they belong to and those teams' notes. Anything
  else returns **404**, not 403, so existence isn't leaked. **403** means the user can see the
  resource but their role isn't high enough.
- **Team creation and membership:** any user can create a team and becomes its admin. Admins
  add existing users by email. Any member can leave a team with
  `DELETE /teams/{id}/members/{own id}`.
- **Admins:** a team always keeps at least one admin. Demoting or removing the last one returns
  `409 last_admin`. Membership changes lock the team row, so this check can't race.
- **Deleting a team:** only allowed when the team has no non-deleted notes. Soft-deleted notes
  and their revisions are removed with the team.
- **Code:** the checks are FastAPI dependencies in `app/api/deps.py`, backed by
  `app/services/access.py`.

## Concurrent edits: versioning and merging

Every note has an integer `version`, which starts at 1 and goes up by one on each saved change.
Every version is also kept as a `NoteRevision` row, written in the same transaction.

**The contract**

1. `GET /api/v1/notes/{id}` returns `ETag: "<version>"`.
2. `PATCH` and `DELETE` must send `If-Match: "<version>"`, the version the edit was based on
   (the **base**). Without it the server returns **428**. A malformed value returns 400.
3. The server locks the note row (`SELECT … FOR UPDATE`), so writers to the same note are
   serialized, and then compares the base with the current version:

| Situation | Result |
| --- | --- |
| base == current | Change applied, new version, **200** + new `ETag` |
| base < current, merges cleanly | Merged result saved as a new version, **200**, `X-Note-Merged: true`, body field `merged_from_versions: [base, current]` |
| base < current, conflicts | **Nothing saved**, **409** with `current`, `merged_proposal`, `conflicts` |
| base > current, or no such revision | **412** |
| `DELETE` with base ≠ current | **412**; deletes are never merged |

**Merge rules.** Only fields present in the PATCH body count as changed by the client:

- **title**: if only one side changed it, that side wins. If both sides changed it to the same
  value, that value is kept. If both changed it to different values, it's a conflict.
- **tags**: `(current ∪ added_by_you) − removed_by_you`, with additions and removals measured
  against the base. Tags never conflict.
- **content**: a line-based three-way merge (`merge3`) after normalizing line endings to `\n`.
  Edits to separate regions merge. Edits to the same or **adjacent** lines conflict, as in
  `diff3`.
- If the result equals the current version, no new version is created.

The merge is a pure function in `app/merge/` (`merge_note(base, current, incoming) ->
MergeResult`). The text merge sits behind the `TextMerger` protocol, so a word-level merger can
replace `LineMerger` later.

**Example: auto-merged edit.** Bob loaded v3, but Alice saved v4 in the meantime:

```http
PATCH /api/v1/notes/3fa85f64-5717-4562-b3fc-2c963f66afa6
Authorization: Bearer …
If-Match: "3"
Content-Type: application/json

{"content": "# Release\n- bump version (Bob)\n- tag release\n- publish notes (Alice)\n"}
```

```http
HTTP/1.1 200 OK
ETag: "5"
X-Note-Merged: true

{
  "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "version": 5,
  "title": "Release checklist",
  "content": "# Release\n- bump version (Bob)\n- tag release\n- publish notes (Alice)\n",
  "tags": ["ops", "release"],
  "merged_from_versions": [3, 4],
  "...": "other note fields"
}
```

**Example: conflicting edit.** Both sides changed the same line. All errors share the envelope
`{"error": {"code", "message", "details"}}`, and for conflicts the merge information goes in
`details`:

```http
HTTP/1.1 409 Conflict

{
  "error": {
    "code": "edit_conflict",
    "message": "Your edit conflicts with changes saved since version 3",
    "details": {
      "base_version": 3,
      "current": { "version": 4, "title": "Release checklist", "content": "…", "...": "full note" },
      "merged_proposal": {
        "title": "Release checklist",
        "content": "# Release\n<<<<<<< v4 (Alice)\n- bump version to 2.0\n=======\n- bump version to 2.1\n>>>>>>> yours (Bob, based on v3)\n- tag release\n",
        "tags": ["ops", "release"]
      },
      "conflicts": [
        {
          "field": "content",
          "base_lines": {"start": 2, "end": 2},
          "proposal_lines": {"start": 2, "end": 6},
          "base": "- bump version\n",
          "theirs": "- bump version to 2.0\n",
          "yours": "- bump version to 2.1\n"
        }
      ]
    }
  }
}
```

A title conflict looks like `{"field": "title", "base": …, "theirs": …, "yours": …}`.
Line numbers are 1-based and inclusive. `proposal_lines` points at the marker block in
`merged_proposal.content`, so a UI can render a resolution view without parsing markers. To
resolve, the client re-submits its resolved fields with `If-Match: "<current.version>"`.

## API overview

All routes are under `/api/v1`. The interactive docs at `/docs` include every schema and the
documented 200/409 examples for `PATCH /notes/{id}`.

| Area | Endpoints |
| --- | --- |
| Health | `GET /healthz` (liveness, no DB), `GET /readyz` (runs `SELECT 1`); also served at `/healthz`, `/readyz` |
| Auth | `POST /auth/login` (OAuth2 form, `username` = email), `POST /auth/refresh`, `POST /auth/register` |
| Users | `GET` / `PATCH /users/me`, `POST /users` and `GET /users/{id}` (superuser) |
| Teams | `GET` / `POST /teams`, `GET` / `PATCH` / `DELETE /teams/{id}` |
| Members | `GET` / `POST /teams/{id}/members`, `PATCH` / `DELETE /teams/{id}/members/{user_id}` |
| Notes | `GET` / `POST /notes`, `GET` / `PATCH` / `DELETE /notes/{id}`, `POST /notes/{id}/restore` |
| Revisions | `GET /notes/{id}/revisions`, `GET /notes/{id}/revisions/{version}` |

`GET /notes` returns **summaries only**: `id`, `team_id`, `title`, `excerpt`, `tags`,
`version`, `updated_at`, `updated_by`, `deleted_at`. The `content` column isn't even loaded.
Query parameters:

- `team_id`
- `q`: full text via `websearch_to_tsquery`, ordered by rank, with title weighted above content
- `tag`: repeatable, matches notes that have all the given tags
- `sort`: `updated_at`, `created_at` or `title`, `-` prefix for descending; default `-updated_at`
- `limit`: 1–100, default 20
- `offset`
- `include_deleted`

The response envelope is `{items, total, limit, offset}`.

Limits:

- **title:** 1–200 characters
- **content:** ≤ 1 MiB of UTF-8
- **tags:** ≤ 20 per note, each ≤ 50 characters, lower-cased, de-duplicated and stored sorted

Every response carries `X-Request-ID`, taken from the request if it is well-formed and
generated otherwise. Logs are structured JSON on stdout and include the request ID.

## Configuration

Everything is configured through environment variables, loaded with pydantic-settings from the
environment or `.env`. `.env.example` documents every variable:

- **Database:** `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_USER`, `POSTGRES_PASSWORD`,
  `POSTGRES_DB`, plus the pool settings
- **Auth:** `JWT_SECRET`, `JWT_ALGORITHM`, `ACCESS_TOKEN_EXPIRE_MINUTES` (15),
  `REFRESH_TOKEN_EXPIRE_MINUTES` (7 days), `ALLOW_REGISTRATION` (default: on in dev, off in prod)
- **HTTP:** `CORS_ORIGINS`
- **General:** `LOG_LEVEL`, `ENVIRONMENT` (`dev` | `prod`)
- **Uvicorn:** `WEB_CONCURRENCY`, `FORWARDED_ALLOW_IPS`

Startup fails fast if `JWT_SECRET` is missing, or if it's weak in prod. Never commit `.env`.

## Project layout

```
app/
  main.py            app factory: routers, middleware, exception handlers
  cli.py             python -m app.cli create-superuser
  core/              settings, JSON logging, request-ID middleware, security (JWT, argon2), errors
  db/                declarative Base + mixins, async engine/session dependency
  models/            SQLAlchemy models (User, Team, TeamMembership, Note, NoteRevision)
  schemas/           Pydantic request/response schemas (separate from the models)
  services/          business logic: access rules, auth, users, teams, notes (versioning/merging)
  merge/             pure three-way merge (no DB/FastAPI imports)
  api/deps.py        current user, team/note access dependencies, If-Match parsing
  api/v1/            thin routers: health, auth, users, teams, notes
migrations/          Alembic (async env) and versions
tests/               pytest + testcontainers
docker/              Dockerfile, web start script
tools/dev.py         cross-platform helper tasks
compose.yaml         base stack · compose.override.yaml (dev, auto-loaded) · compose.prod.yaml
```

The spec's Traefik config lives in the compose files rather than in `docker/`. Traefik is
configured through environment variables and labels, so the dev and prod files can each add
settings without duplicating a static config file.
