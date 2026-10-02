"""Unit tests that need no database: excerpts, schema normalization, settings, tokens."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import Environment, Settings
from app.core.security import InvalidTokenError, TokenType, create_token, decode_token
from app.schemas.note import MAX_CONTENT_BYTES, NoteCreate, NoteUpdate
from app.services.text import make_excerpt, markdown_to_plain

SECRET = SecretStr("x" * 40)


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {"_env_file": None, "jwt_secret": SECRET}
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


# --- excerpt -----------------------------------------------------------------------------------


def test_markdown_is_stripped_for_excerpt() -> None:
    markdown = (
        "# Heading\n\nSome **bold** and _italic_ text with a [link](https://x.y) and "
        "`code`.\n\n> quoted\n\n- item one\n- [x] done\n1. first\n\n![alt text](img.png)\n\n"
        "```python\nprint('hi')\n```\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n---\n<b>html</b>"
    )
    assert markdown_to_plain(markdown) == (
        "Heading Some bold and italic text with a link and code. quoted item one done first "
        "alt text print('hi') a b 1 2 html"
    )


def test_excerpt_is_truncated_at_word_boundary() -> None:
    excerpt = make_excerpt("word " * 100)
    assert len(excerpt) <= 200
    assert excerpt.endswith("…")
    assert not excerpt[:-1].endswith(" ")


def test_short_excerpt_is_unchanged() -> None:
    assert make_excerpt("Hello *world*") == "Hello world"


# --- schemas -----------------------------------------------------------------------------------


def test_tags_are_normalized_deduplicated_and_sorted() -> None:
    note = NoteCreate(team_id=uuid.uuid4(), title="  T  ", tags=["Beta", " alpha", "BETA"])
    assert note.title == "T"
    assert note.tags == ["alpha", "beta"]


@pytest.mark.parametrize(
    "tags",
    [[f"t{i}" for i in range(21)], ["x" * 51], [""], ["a,b"]],
    ids=["too-many", "too-long", "empty", "comma"],
)
def test_invalid_tags_rejected(tags: list[str]) -> None:
    with pytest.raises(ValidationError):
        NoteCreate(team_id=uuid.uuid4(), title="T", tags=tags)


def test_twenty_distinct_tags_after_dedup_are_fine() -> None:
    tags = [f"t{i}" for i in range(20)] + ["T0", "T1"]
    assert len(NoteCreate(team_id=uuid.uuid4(), title="T", tags=tags).tags) == 20


@pytest.mark.parametrize("title", ["", "   ", "x" * 201])
def test_invalid_titles_rejected(title: str) -> None:
    with pytest.raises(ValidationError):
        NoteCreate(team_id=uuid.uuid4(), title=title)


def test_content_limit_is_measured_in_utf8_bytes() -> None:
    NoteCreate(team_id=uuid.uuid4(), title="T", content="a" * MAX_CONTENT_BYTES)
    with pytest.raises(ValidationError):
        NoteCreate(team_id=uuid.uuid4(), title="T", content="é" * (MAX_CONTENT_BYTES // 2 + 1))


def test_content_line_endings_are_normalized() -> None:
    assert NoteCreate(team_id=uuid.uuid4(), title="T", content="a\r\nb\rc").content == "a\nb\nc"


def test_note_update_requires_a_field_and_rejects_null() -> None:
    with pytest.raises(ValidationError):
        NoteUpdate.model_validate({})
    with pytest.raises(ValidationError):
        NoteUpdate.model_validate({"title": None})
    assert NoteUpdate.model_validate({"tags": []}).model_fields_set == {"tags"}


# --- settings ----------------------------------------------------------------------------------


def test_registration_defaults_depend_on_environment() -> None:
    assert make_settings(environment=Environment.DEV).registration_enabled is True
    assert make_settings(environment=Environment.PROD).registration_enabled is False
    assert make_settings(environment=Environment.PROD, allow_registration=True).registration_enabled


@pytest.mark.parametrize("secret", ["short", "change-me"])
def test_weak_jwt_secret_fails_fast_in_prod(secret: str) -> None:
    with pytest.raises(ValidationError, match="JWT_SECRET"):
        make_settings(environment=Environment.PROD, jwt_secret=SecretStr(secret))


def test_missing_jwt_secret_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JWT_SECRET", raising=False)
    with pytest.raises(ValidationError, match="jwt_secret"):
        Settings(_env_file=None)


def test_cors_origins_accept_comma_separated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "https://a.example, https://b.example")
    settings = make_settings()
    assert settings.cors_origins == ["https://a.example", "https://b.example"]


def test_database_url_escapes_password() -> None:
    url = make_settings(postgres_password=SecretStr("p@ss/word")).database_url
    assert url.password == "p@ss/word"
    assert "p%40ss%2Fword" in url.render_as_string(hide_password=False)


# --- tokens ------------------------------------------------------------------------------------


def test_token_round_trip_and_type_check() -> None:
    settings = make_settings()
    user_id = uuid.uuid4()
    token, _ = create_token(settings, user_id, TokenType.ACCESS)
    assert decode_token(settings, token, TokenType.ACCESS).subject == user_id
    with pytest.raises(InvalidTokenError):
        decode_token(settings, token, TokenType.REFRESH)


def test_expired_and_foreign_tokens_are_rejected() -> None:
    settings = make_settings()
    old = datetime.now(UTC) - timedelta(days=30)
    expired, _ = create_token(settings, uuid.uuid4(), TokenType.ACCESS, now=old)
    with pytest.raises(InvalidTokenError):
        decode_token(settings, expired, TokenType.ACCESS)

    other = make_settings(jwt_secret=SecretStr("y" * 40))
    foreign, _ = create_token(other, uuid.uuid4(), TokenType.ACCESS)
    with pytest.raises(InvalidTokenError):
        decode_token(settings, foreign, TokenType.ACCESS)
