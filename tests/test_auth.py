import uuid
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI
from httpx import AsyncClient

from app.core.security import TokenType, create_token
from tests.conftest import DEFAULT_PASSWORD, MakeUser


async def test_login_returns_token_pair(client: AsyncClient, make_user: MakeUser) -> None:
    user = await make_user("Dana")
    response = await client.post(
        "/api/v1/auth/login",
        data={"username": user.email.upper(), "password": DEFAULT_PASSWORD},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 15 * 60
    assert body["refresh_expires_in"] == 7 * 24 * 3600

    me = await client.get(
        "/api/v1/users/me", headers={"Authorization": f"Bearer {body['access_token']}"}
    )
    assert me.status_code == 200
    assert me.json()["email"] == user.email


async def test_login_rejects_bad_credentials(client: AsyncClient, make_user: MakeUser) -> None:
    user = await make_user()
    for username, password in [(user.email, "wrong-password"), ("nobody@example.com", "x" * 10)]:
        response = await client.post(
            "/api/v1/auth/login", data={"username": username, "password": password}
        )
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "invalid_credentials"
        assert response.headers["WWW-Authenticate"] == "Bearer"


async def test_inactive_user_cannot_login_or_use_tokens(
    client: AsyncClient, make_user: MakeUser
) -> None:
    user = await make_user(active=False)
    login = await client.post(
        "/api/v1/auth/login", data={"username": user.email, "password": DEFAULT_PASSWORD}
    )
    assert login.status_code == 401
    assert (await client.get("/api/v1/users/me", headers=user.headers)).status_code == 401


async def test_refresh_issues_new_pair(client: AsyncClient, make_user: MakeUser) -> None:
    user = await make_user()
    response = await client.post("/api/v1/auth/refresh", json={"refresh_token": user.refresh_token})
    assert response.status_code == 200
    new_access = response.json()["access_token"]
    me = await client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {new_access}"})
    assert me.status_code == 200


async def test_refresh_rejects_access_token_and_garbage(
    client: AsyncClient, make_user: MakeUser
) -> None:
    user = await make_user()
    for token in (user.access_token, "not-a-jwt"):
        response = await client.post("/api/v1/auth/refresh", json={"refresh_token": token})
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "invalid_token"


async def test_refresh_token_cannot_be_used_as_access_token(
    client: AsyncClient, make_user: MakeUser
) -> None:
    user = await make_user()
    response = await client.get(
        "/api/v1/users/me", headers={"Authorization": f"Bearer {user.refresh_token}"}
    )
    assert response.status_code == 401


async def test_expired_and_invalid_access_tokens(
    client: AsyncClient, app: FastAPI, make_user: MakeUser
) -> None:
    user = await make_user()
    expired, _ = create_token(
        app.state.settings,
        user.id,
        TokenType.ACCESS,
        now=datetime.now(UTC) - timedelta(hours=1),
    )
    unknown_user, _ = create_token(app.state.settings, uuid.uuid4(), TokenType.ACCESS)
    for token in (expired, unknown_user, user.access_token + "x"):
        response = await client.get(
            "/api/v1/users/me", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 401, token
        assert response.json()["error"]["code"] == "invalid_token"

    missing = await client.get("/api/v1/users/me")
    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "unauthorized"


async def test_registration_when_enabled(client: AsyncClient) -> None:
    payload = {"email": "New@Example.com", "display_name": "New", "password": "s3cret-pass"}
    response = await client.post("/api/v1/auth/register", json=payload)
    assert response.status_code == 201
    assert response.headers["Location"] == "/api/v1/users/me"
    assert response.json()["email"] == "new@example.com"
    assert "password_hash" not in response.json()

    duplicate = await client.post(
        "/api/v1/auth/register", json={**payload, "email": "NEW@example.COM"}
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "email_taken"


async def test_registration_toggle(client: AsyncClient, app: FastAPI) -> None:
    settings = app.state.settings
    original = settings.allow_registration
    settings.allow_registration = False
    try:
        response = await client.post(
            "/api/v1/auth/register",
            json={"email": "x@example.com", "display_name": "X", "password": "s3cret-pass"},
        )
    finally:
        settings.allow_registration = original
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "registration_disabled"


async def test_registration_validation_errors_use_envelope(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": "not-an-email", "display_name": "", "password": "pw-Zq9"},
    )
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    fields = {tuple(d["loc"]) for d in error["details"]}
    assert {("body", "email"), ("body", "display_name"), ("body", "password")} <= fields
    assert "pw-Zq9" not in response.text  # inputs (e.g. passwords) are not echoed
