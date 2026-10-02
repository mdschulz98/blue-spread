"""Application factory. Run with ``uvicorn app.main:create_app --factory``."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import api_router
from app.core.config import Settings, get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging
from app.core.middleware import REQUEST_ID_HEADER, RequestContextMiddleware
from app.db.session import create_engine, create_sessionmaker

DESCRIPTION = """
Backend for a team note-taking service.

* Authenticate with `POST /api/v1/auth/login` (OAuth2 password form; `username` = email) and
  send `Authorization: Bearer <access_token>`.
* Notes belong to one team. Roles: **viewer** (read), **editor** (write), **admin** (manage).
* Edits use optimistic concurrency with automatic three-way merging — see `PATCH /notes/{id}`.
* Every error has the shape `{"error": {"code", "message", "details"}}`.
"""


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    engine = create_engine(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await engine.dispose()

    app = FastAPI(
        title="Notes API",
        version="0.1.0",
        description=DESCRIPTION,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.sessionmaker = create_sessionmaker(engine)

    register_exception_handlers(app)

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["ETag", "Location", "X-Note-Merged", REQUEST_ID_HEADER],
        )
    # Outermost, so every response (including errors and CORS preflights) gets a request ID.
    app.add_middleware(RequestContextMiddleware)

    app.include_router(api_router)
    return app
