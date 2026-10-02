"""Test harness: a real PostgreSQL 17 (testcontainers), migrated with Alembic once per session.

Each test that uses ``client`` gets a clean database: all tables are truncated afterwards.
(Rollback-per-test isn't used because services commit and some tests need concurrent
connections to exercise row locking.)
"""

import itertools
import os
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from pydantic import SecretStr
from sqlalchemy import text

# Ryuk (the testcontainers reaper) only matters if the test process is killed; the fixture
# below stops its container on normal exit and Ctrl-C. testcontainers 4.15 races on Ryuk's port
# mapping on Docker Desktop, so it is off unless explicitly enabled.
os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")

from testcontainers.community.postgres import PostgresContainer

from app.core.config import Environment, Settings
from app.main import create_app
from app.services import users as user_service

ROOT = Path(__file__).resolve().parent.parent
TEST_JWT_SECRET = "test-secret-that-is-long-enough-for-hs256-0123456789"
DEFAULT_PASSWORD = "correct-horse-battery"


@pytest.fixture(scope="session")
def postgres() -> Iterator[PostgresContainer]:
    with PostgresContainer("postgres:17", driver=None) as container:
        yield container


@pytest.fixture(scope="session")
def settings(postgres: PostgresContainer) -> Settings:
    return Settings(
        _env_file=None,
        environment=Environment.DEV,
        log_level="WARNING",
        postgres_host=postgres.get_container_host_ip(),
        postgres_port=int(postgres.get_exposed_port(5432)),
        postgres_user=postgres.username,
        postgres_password=SecretStr(postgres.password),
        postgres_db=postgres.dbname,
        jwt_secret=SecretStr(TEST_JWT_SECRET),
        allow_registration=True,
    )


def alembic_config(settings: Settings) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.attributes["database_url"] = settings.database_url
    config.attributes["configure_logger"] = False
    return config


@pytest.fixture(scope="session")
def migrated(settings: Settings) -> None:
    # env.py calls asyncio.run(); keep that off pytest-asyncio's event loop thread.
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(command.upgrade, alembic_config(settings), "head").result()


@pytest.fixture(scope="session")
async def app(settings: Settings, migrated: None) -> AsyncIterator[FastAPI]:
    application = create_app(settings)
    yield application
    await application.state.engine.dispose()


async def truncate_all(app: FastAPI) -> None:
    async with app.state.engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE note_revisions, notes, team_memberships, teams, users "
                "RESTART IDENTITY CASCADE"
            )
        )


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http
    await truncate_all(app)


# --- users -----------------------------------------------------------------------------------


@dataclass
class TestUser:
    __test__ = False  # not a test class, despite the name

    id: uuid.UUID
    email: str
    display_name: str
    password: str
    access_token: str
    refresh_token: str

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}"}


MakeUser = Callable[..., Awaitable[TestUser]]
_user_counter = itertools.count(1)


@pytest.fixture
def make_user(app: FastAPI, client: AsyncClient) -> MakeUser:
    async def _make(
        name: str | None = None, *, superuser: bool = False, active: bool = True
    ) -> TestUser:
        n = next(_user_counter)
        display_name = name or f"User {n}"
        email = f"{(name or 'user').lower().replace(' ', '.')}.{n}@example.com"
        async with app.state.sessionmaker() as session:
            user = await user_service.create_user(
                session,
                email=email,
                display_name=display_name,
                password=DEFAULT_PASSWORD,
                is_superuser=superuser,
            )
        response = await client.post(
            "/api/v1/auth/login", data={"username": email, "password": DEFAULT_PASSWORD}
        )
        assert response.status_code == 200, response.text
        tokens = response.json()
        if not active:
            async with app.state.sessionmaker() as session:
                db_user = await user_service.get_user(session, user.id)
                db_user.is_active = False
                await session.commit()
        return TestUser(
            id=user.id,
            email=email,
            display_name=display_name,
            password=DEFAULT_PASSWORD,
            access_token=tokens["access_token"],
            refresh_token=tokens["refresh_token"],
        )

    return _make


# --- teams -----------------------------------------------------------------------------------


@dataclass
class TeamFixture:
    id: uuid.UUID
    admin: TestUser
    editor: TestUser
    viewer: TestUser
    outsider: TestUser


async def create_team(client: AsyncClient, owner: TestUser, name: str | None = None) -> uuid.UUID:
    response = await client.post(
        "/api/v1/teams",
        json={"name": name or f"Team {uuid.uuid4().hex[:8]}"},
        headers=owner.headers,
    )
    assert response.status_code == 201, response.text
    return uuid.UUID(response.json()["id"])


async def add_member(
    client: AsyncClient, team_id: uuid.UUID, admin: TestUser, user: TestUser, role: str
) -> None:
    response = await client.post(
        f"/api/v1/teams/{team_id}/members",
        json={"email": user.email, "role": role},
        headers=admin.headers,
    )
    assert response.status_code == 201, response.text


@pytest.fixture
async def team(client: AsyncClient, make_user: MakeUser) -> TeamFixture:
    admin = await make_user("Alice")
    editor = await make_user("Bob")
    viewer = await make_user("Carol")
    outsider = await make_user("Mallory")
    team_id = await create_team(client, admin, "Platform")
    await add_member(client, team_id, admin, editor, "editor")
    await add_member(client, team_id, admin, viewer, "viewer")
    return TeamFixture(id=team_id, admin=admin, editor=editor, viewer=viewer, outsider=outsider)


# --- notes -----------------------------------------------------------------------------------


async def create_note(
    client: AsyncClient,
    user: TestUser,
    team_id: uuid.UUID,
    *,
    title: str = "A note",
    content: str = "",
    tags: list[str] | None = None,
) -> dict[str, Any]:
    response = await client.post(
        "/api/v1/notes",
        json={"team_id": str(team_id), "title": title, "content": content, "tags": tags or []},
        headers=user.headers,
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


async def patch_note(
    client: AsyncClient,
    user: TestUser,
    note_id: object,
    base_version: int | None,
    body: Mapping[str, Any],
) -> Response:
    headers = dict(user.headers)
    if base_version is not None:
        headers["If-Match"] = f'"{base_version}"'
    return await client.patch(f"/api/v1/notes/{note_id}", json=body, headers=headers)
