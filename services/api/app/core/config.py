"""Typed application configuration loaded from environment variables."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings without embedding secrets in source code."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: Literal["development", "test", "production"] = "development"
    service_name: str = Field(default="socialpilot-api", min_length=1, max_length=64)
    log_level: str = Field(default="INFO", min_length=1, max_length=16)
    database_url: str = Field(min_length=1)
    postgres_port: int = Field(default=5432, ge=1, le=65535)
    database_pool_size: int = Field(default=5, ge=1, le=100)
    database_max_overflow: int = Field(default=10, ge=0, le=100)
    redis_url: str = Field(min_length=1)
    redis_port: int = Field(default=6379, ge=1, le=65535)
    database_connect_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    redis_connect_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    request_timeout_seconds: float = Field(default=10.0, gt=0, le=120)
    celery_broker_url: str = Field(min_length=1)
    celery_result_backend: str = Field(min_length=1)
    celery_task_timeout_seconds: int = Field(default=300, gt=0, le=3600)
    media_max_bytes: int = Field(default=104_857_600, gt=0, le=2_147_483_647)
    media_max_parts: int = Field(default=100, ge=1, le=1_000)
    media_upload_session_ttl_seconds: int = Field(default=900, ge=60, le=3_600)
    media_allowed_mime_types: tuple[str, ...] = (
        "image/jpeg",
        "image/png",
        "video/mp4",
        "audio/mpeg",
    )
    identity_adapter: Literal["local"] = "local"
    local_identity_signing_key: SecretStr = Field(
        default=SecretStr("development-local-identity-key-not-for-production"),
        min_length=32,
    )

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: str) -> str:
        if not value.startswith("postgresql+asyncpg://"):
            raise ValueError("DATABASE_URL must use the postgresql+asyncpg scheme")
        return value

    @field_validator("redis_url", "celery_broker_url", "celery_result_backend")
    @classmethod
    def validate_redis_url(cls, value: str) -> str:
        if not value.startswith(("redis://", "rediss://")):
            raise ValueError("Redis URLs must use redis:// or rediss://")
        return value

    @field_validator("media_allowed_mime_types")
    @classmethod
    def validate_media_types(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or any("/" not in mime for mime in value):
            raise ValueError("MEDIA_ALLOWED_MIME_TYPES must contain MIME types")
        return tuple(mime.lower() for mime in value)

    @model_validator(mode="after")
    def reject_local_only_production_urls(self) -> Settings:
        if self.app_env == "production" and "local_only" in self.database_url:
            raise ValueError("production DATABASE_URL cannot use a local-only credential")
        return self

    @model_validator(mode="after")
    def reject_local_identity_adapter_in_production(self) -> Settings:
        if self.app_env == "production" and self.identity_adapter == "local":
            raise ValueError("the local identity adapter is not allowed in production")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return one immutable parsed settings object for the process."""

    return Settings()  # type: ignore[call-arg]
