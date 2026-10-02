"""Initial schema: users, teams, memberships, notes and note revisions.

Revision ID: 826c4fb1b2cd
Revises:
Create Date: 2026-10-02 09:06:18.081886

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# Weighted full-text vector: title (A) + the first 256 KiB of content (B).
SEARCH_VECTOR_EXPRESSION = (
    "setweight(to_tsvector('english'::regconfig, coalesce(title, '')), 'A') || "
    "setweight(to_tsvector('english'::regconfig, left(coalesce(content, ''), 262144)), 'B')"
)

# revision identifiers, used by Alembic.
revision: str = "826c4fb1b2cd"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "teams",
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_teams")),
    )
    op.create_index("uq_teams_name_lower", "teams", [sa.literal_column("lower(name)")], unique=True)
    op.create_table(
        "users",
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("display_name", sa.String(length=100), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("is_superuser", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
    )
    op.create_index(
        "uq_users_email_lower", "users", [sa.literal_column("lower(email)")], unique=True
    )
    op.create_table(
        "notes",
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "tags",
            postgresql.ARRAY(sa.String(length=50)),
            server_default=sa.text("'{}'::varchar[]"),
            nullable=False,
        ),
        sa.Column("excerpt", sa.String(length=300), server_default="", nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("created_by_id", sa.Uuid(), nullable=False),
        sa.Column("updated_by_id", sa.Uuid(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by_id", sa.Uuid(), nullable=True),
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR(),
            sa.Computed(
                SEARCH_VECTOR_EXPRESSION,
                persisted=True,
            ),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"], ["users.id"], name=op.f("fk_notes_created_by_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["deleted_by_id"], ["users.id"], name=op.f("fk_notes_deleted_by_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["team_id"], ["teams.id"], name=op.f("fk_notes_team_id_teams"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_id"], ["users.id"], name=op.f("fk_notes_updated_by_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notes")),
    )
    op.create_index(
        "ix_notes_search_vector", "notes", ["search_vector"], unique=False, postgresql_using="gin"
    )
    op.create_index("ix_notes_tags", "notes", ["tags"], unique=False, postgresql_using="gin")
    op.create_index("ix_notes_team_id_updated_at", "notes", ["team_id", "updated_at"], unique=False)
    op.create_table(
        "team_memberships",
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.Enum("viewer", "editor", "admin", name="team_role"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["teams.id"],
            name=op.f("fk_team_memberships_team_id_teams"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_team_memberships_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("team_id", "user_id", name=op.f("pk_team_memberships")),
    )
    op.create_index("ix_team_memberships_user_id", "team_memberships", ["user_id"], unique=False)
    op.create_table(
        "note_revisions",
        sa.Column("note_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("tags", postgresql.ARRAY(sa.String(length=50)), nullable=False),
        sa.Column("author_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["author_id"], ["users.id"], name=op.f("fk_note_revisions_author_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["note_id"],
            ["notes.id"],
            name=op.f("fk_note_revisions_note_id_notes"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("note_id", "version", name=op.f("pk_note_revisions")),
    )
    op.create_index("ix_note_revisions_author_id", "note_revisions", ["author_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_note_revisions_author_id", table_name="note_revisions")
    op.drop_table("note_revisions")
    op.drop_index("ix_team_memberships_user_id", table_name="team_memberships")
    op.drop_table("team_memberships")
    op.drop_index("ix_notes_team_id_updated_at", table_name="notes")
    op.drop_index("ix_notes_tags", table_name="notes", postgresql_using="gin")
    op.drop_index("ix_notes_search_vector", table_name="notes", postgresql_using="gin")
    op.drop_table("notes")
    op.drop_index("uq_users_email_lower", table_name="users")
    op.drop_table("users")
    op.drop_index("uq_teams_name_lower", table_name="teams")
    op.drop_table("teams")
    sa.Enum(name="team_role").drop(op.get_bind(), checkfirst=True)
