"""Smoke test of a running stack, through Traefik. Standard library only, so CI can run it
with the runner's Python and no project dependencies.

    uv run poe smoke                     # against the local stack (http://api.localhost)
    python3 tools/smoke.py               # same, with any Python >= 3.10 (CI uses the runner's)
    SMOKE_BASE_URL=http://127.0.0.1 SMOKE_HOST=api.example.com python3 tools/smoke.py

Exits non-zero (and prints what failed) unless every check passes.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any

BASE_URL = os.environ.get("SMOKE_BASE_URL", "http://127.0.0.1").rstrip("/")
# Requests go to BASE_URL with this Host header, so *.localhost needn't resolve on the client.
HOST = os.environ.get("SMOKE_HOST", "api.localhost")
WAIT_SECONDS = float(os.environ.get("SMOKE_WAIT_SECONDS", "90"))


class Response:
    def __init__(self, status: int, headers: dict[str, str], body: bytes) -> None:
        self.status = status
        self.headers = headers
        self.body = body

    def json(self) -> Any:
        return json.loads(self.body)


def request(
    method: str,
    path: str,
    *,
    host: str = HOST,
    json_body: Any = None,
    form: dict[str, str] | None = None,
    token: str | None = None,
) -> Response:
    headers = {"Host": host}
    data = None
    if json_body is not None:
        data = json.dumps(json_body).encode()
        headers["Content-Type"] = "application/json"
    elif form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = BASE_URL + path
    req = urllib.request.Request(url, data=data, headers=headers, method=method)  # noqa: S310
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 - URL from config
            return Response(
                resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read()
            )
    except urllib.error.HTTPError as err:
        return Response(err.code, {k.lower(): v for k, v in err.headers.items()}, err.read())


def wait_until_healthy() -> None:
    deadline = time.monotonic() + WAIT_SECONDS
    last = "no response"
    while time.monotonic() < deadline:
        try:
            response = request("GET", "/api/v1/healthz")
            if response.status == 200:
                return
            last = f"HTTP {response.status}"
        except OSError as exc:  # connection refused/reset while the stack starts
            last = str(exc)
        time.sleep(2)
    raise SystemExit(f"FAIL: API not healthy through Traefik after {WAIT_SECONDS:.0f}s ({last})")


def main() -> int:
    print(f"Smoke testing {BASE_URL} (Host: {HOST})")
    wait_until_healthy()
    failures: list[str] = []

    def check(name: str, ok: bool, detail: object = "") -> None:
        print(f"  {'ok  ' if ok else 'FAIL'} {name}" + ("" if ok else f": {detail}"))
        if not ok:
            failures.append(name)

    health = request("GET", "/api/v1/healthz")
    check("liveness 200", health.status == 200, health.status)
    check("X-Request-ID header", bool(health.headers.get("x-request-id")), health.headers)

    ready = request("GET", "/api/v1/readyz")
    check(
        "readiness 200 with database ok",
        ready.status == 200 and ready.json().get("database") == "ok",
        (ready.status, ready.body[:200]),
    )

    docs = request("GET", "/docs")
    check("Swagger UI 200", docs.status == 200, docs.status)
    openapi = request("GET", "/openapi.json")
    check(
        "OpenAPI documents PATCH /notes/{note_id}",
        openapi.status == 200
        and "patch" in openapi.json()["paths"].get("/api/v1/notes/{note_id}", {}),
        openapi.status,
    )

    unknown = request("GET", "/api/v1/healthz", host="unknown.invalid")
    check("unknown Host is not routed (404)", unknown.status == 404, unknown.status)

    # Database round trip through migrations: register (if enabled), log in, read /users/me.
    email = f"smoke-{uuid.uuid4().hex[:12]}@example.com"
    password = uuid.uuid4().hex
    register = request(
        "POST",
        "/api/v1/auth/register",
        json_body={"email": email, "display_name": "Smoke Test", "password": password},
    )
    if register.status == 403:
        print("  skip auth round trip (registration disabled)")
    else:
        check("register 201", register.status == 201, (register.status, register.body[:200]))
        login = request(
            "POST", "/api/v1/auth/login", form={"username": email, "password": password}
        )
        check("login 200", login.status == 200, (login.status, login.body[:200]))
        if login.status == 200:
            me = request("GET", "/api/v1/users/me", token=login.json()["access_token"])
            check("GET /users/me", me.status == 200 and me.json().get("email") == email, me.status)

    if failures:
        print(f"{len(failures)} check(s) failed")
        return 1
    print("All smoke checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
