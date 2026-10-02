from httpx import AsyncClient

from tests.conftest import DEFAULT_PASSWORD, MakeUser


async def test_update_display_name(client: AsyncClient, make_user: MakeUser) -> None:
    user = await make_user()
    response = await client.patch(
        "/api/v1/users/me", json={"display_name": "  Renamed  "}, headers=user.headers
    )
    assert response.status_code == 200
    assert response.json()["display_name"] == "Renamed"


async def test_change_password_requires_correct_current_password(
    client: AsyncClient, make_user: MakeUser
) -> None:
    user = await make_user()
    missing = await client.patch(
        "/api/v1/users/me", json={"new_password": "brand-new-pass"}, headers=user.headers
    )
    assert missing.status_code == 422

    wrong = await client.patch(
        "/api/v1/users/me",
        json={"current_password": "nope-nope", "new_password": "brand-new-pass"},
        headers=user.headers,
    )
    assert wrong.status_code == 400
    assert wrong.json()["error"]["code"] == "invalid_password"

    ok = await client.patch(
        "/api/v1/users/me",
        json={"current_password": DEFAULT_PASSWORD, "new_password": "brand-new-pass"},
        headers=user.headers,
    )
    assert ok.status_code == 200
    old_login = await client.post(
        "/api/v1/auth/login", data={"username": user.email, "password": DEFAULT_PASSWORD}
    )
    new_login = await client.post(
        "/api/v1/auth/login", data={"username": user.email, "password": "brand-new-pass"}
    )
    assert old_login.status_code == 401
    assert new_login.status_code == 200


async def test_superuser_creates_users(client: AsyncClient, make_user: MakeUser) -> None:
    admin = await make_user(superuser=True)
    response = await client.post(
        "/api/v1/users",
        json={"email": "made@example.com", "display_name": "Made", "password": "s3cret-pass"},
        headers=admin.headers,
    )
    assert response.status_code == 201
    created = response.json()
    assert response.headers["Location"] == f"/api/v1/users/{created['id']}"
    fetched = await client.get(response.headers["Location"], headers=admin.headers)
    assert fetched.status_code == 200
    assert fetched.json()["email"] == "made@example.com"


async def test_regular_user_cannot_create_users(client: AsyncClient, make_user: MakeUser) -> None:
    user = await make_user()
    response = await client.post(
        "/api/v1/users",
        json={"email": "made@example.com", "display_name": "Made", "password": "s3cret-pass"},
        headers=user.headers,
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"
