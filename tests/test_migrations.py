"""The migrations must produce exactly the schema the models describe."""

from typing import Any

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from fastapi import FastAPI
from sqlalchemy import inspect
from sqlalchemy.engine import Connection

from app.db.base import Base


def _diff(connection: Connection) -> list[Any]:
    context = MigrationContext.configure(
        connection, opts={"compare_type": True, "compare_server_default": True}
    )
    return list(compare_metadata(context, Base.metadata))


async def test_models_match_migrations(app: FastAPI) -> None:
    async with app.state.engine.connect() as conn:
        diff = await conn.run_sync(_diff)
    assert diff == []


async def test_search_vector_is_generated_column_with_gin_indexes(app: FastAPI) -> None:
    def inspect_notes(connection: Connection) -> tuple[dict[str, Any], list[Any]]:
        insp = inspect(connection)
        columns = {c["name"]: c for c in insp.get_columns("notes")}
        return columns, insp.get_indexes("notes")

    async with app.state.engine.connect() as conn:
        columns, indexes = await conn.run_sync(inspect_notes)
    assert "computed" in columns["search_vector"]
    gin = {
        i["name"] for i in indexes if i.get("dialect_options", {}).get("postgresql_using") == "gin"
    }
    assert {"ix_notes_search_vector", "ix_notes_tags"} <= gin
