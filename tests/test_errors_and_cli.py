from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import IntegrityError

from app import cli
from app.core.config import Settings
from app.main import create_app
from tests.conftest import DEFAULT_PASSWORD, truncate_all


@pytest.fixture
async def broken_client(settings: Settings, migrated: None) -> AsyncIterator[AsyncClient]:
    """A separate app instance with routes that fail in specific ways."""
    app = create_app(settings)

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("kaboom")

    @app.get("/integrity")
    async def integrity() -> None:
        raise IntegrityError("INSERT ...", {}, Exception("duplicate key"))

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    await app.state.engine.dispose()


async def test_unhandled_error_uses_envelope_and_request_id(broken_client: AsyncClient) -> None:
    response = await broken_client.get("/boom", headers={"X-Request-ID": "req-500"})
    assert response.status_code == 500
    assert response.json() == {
        "error": {"code": "internal_error", "message": "Internal server error", "details": None}
    }
    assert response.headers["X-Request-ID"] == "req-500"
    assert "kaboom" not in response.text


async def test_integrity_error_maps_to_409(broken_client: AsyncClient) -> None:
    response = await broken_client.get("/integrity")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"


async def test_method_not_allowed_uses_envelope(client: AsyncClient) -> None:
    response = await client.put("/api/v1/notes")
    assert response.status_code == 405
    assert response.json()["error"]["code"] == "method_not_allowed"


async def test_cli_creates_and_promotes_superuser(
    app: FastAPI, client: AsyncClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    argv = ["create-superuser", "--email", "Root@Example.com", "--display-name", "Root"]
    with ThreadPoolExecutor(max_workers=1) as pool:  # the CLI runs its own event loop
        assert pool.submit(cli.main, [*argv, "--password", DEFAULT_PASSWORD]).result() == 0
        assert pool.submit(cli.main, [*argv, "--password", DEFAULT_PASSWORD]).result() == 0
        assert pool.submit(cli.main, [*argv, "--password", "short"]).result() == 2

    login = await client.post(
        "/api/v1/auth/login", data={"username": "root@example.com", "password": DEFAULT_PASSWORD}
    )
    assert login.status_code == 200
    me = await client.get(
        "/api/v1/users/me", headers={"Authorization": f"Bearer {login.json()['access_token']}"}
    )
    assert me.json()["is_superuser"] is True
    await truncate_all(app)


async def test_openapi_documents_merge_contract(client: AsyncClient) -> None:
    schema = (await client.get("/openapi.json")).json()
    patch = schema["paths"]["/api/v1/notes/{note_id}"]["patch"]
    assert {"200", "409", "412", "428"} <= set(patch["responses"])
    assert "three-way" in patch["description"]
    assert "X-Note-Merged" in patch["responses"]["200"]["headers"]
    conflict = patch["responses"]["409"]["content"]["application/json"]
    assert conflict["schema"]["$ref"].endswith("/EditConflictResponse")
    assert conflict["example"]["error"]["code"] == "edit_conflict"
    assert "/healthz" not in schema["paths"]  # root probes are hidden; /api/v1 ones are listed
    assert "/api/v1/healthz" in schema["paths"]
