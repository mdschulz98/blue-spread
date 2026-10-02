"""Application settings, loaded from environment variables (and an optional ``.env`` file)."""

from enum import StrEnum
from functools import lru_cache
from typing import Annotated, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy.engine import URL

MIN_PROD_SECRET_LENGTH = 32
_PLACEHOLDER_SECRETS = frozenset({"change-me", "changeme", "secret", "dev-secret"})


class Environment(StrEnum):
    DEV = "dev"
    PROD = "prod"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    environment: Environment = Environment.DEV
    log_level: str = "INFO"

    # Database connection parts. DATABASE_URL is intentionally not accepted so that
    # passwords never need URL-escaping in env files.
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "notes"
    postgres_password: SecretStr = SecretStr("notes")
    postgres_db: str = "notes"
    db_pool_size: int = Field(default=5, ge=1)
    db_max_overflow: int = Field(default=10, ge=0)
    db_echo: bool = False

    # Auth
    jwt_secret: SecretStr
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = Field(default=15, gt=0)
    refresh_token_expire_minutes: int = Field(default=7 * 24 * 60, gt=0)
    # None means "derive from ENVIRONMENT": enabled in dev, disabled in prod.
    allow_registration: bool | None = None

    # HTTP
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        """Accept a comma-separated string (``a,b``) as well as a list."""
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("log_level")
    @classmethod
    def _upper_log_level(cls, value: str) -> str:
        return value.upper()

    @model_validator(mode="after")
    def _finalize(self) -> Self:
        if self.allow_registration is None:
            self.allow_registration = self.environment is Environment.DEV
        if self.is_prod:
            secret = self.jwt_secret.get_secret_value()
            if len(secret) < MIN_PROD_SECRET_LENGTH or secret.lower() in _PLACEHOLDER_SECRETS:
                raise ValueError(
                    f"JWT_SECRET is too weak for prod: use at least {MIN_PROD_SECRET_LENGTH} "
                    'random characters (e.g. `python -c "import secrets; '
                    'print(secrets.token_urlsafe(48))"`)'
                )
        elif not self.jwt_secret.get_secret_value():
            raise ValueError("JWT_SECRET must not be empty")
        return self

    @property
    def is_prod(self) -> bool:
        return self.environment is Environment.PROD

    @property
    def registration_enabled(self) -> bool:
        return bool(self.allow_registration)

    @property
    def database_url(self) -> URL:
        return URL.create(
            drivername="postgresql+asyncpg",
            username=self.postgres_user,
            password=self.postgres_password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )


@lru_cache
def get_settings() -> Settings:
    """Process-wide settings singleton. Raises ``ValidationError`` on bad config (fail fast)."""
    return Settings()  # required fields come from the environment
