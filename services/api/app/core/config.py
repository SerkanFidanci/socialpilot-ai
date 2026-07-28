"""Typed application configuration loaded from environment variables."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PCM_WAV_HEADER_BYTES = 44
PCM_AUDIO_SAMPLE_RATE_HZ = 16_000
PCM_AUDIO_CHANNELS = 1
PCM_AUDIO_BYTES_PER_SAMPLE = 2


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
    media_ingest_timeout_seconds: int = Field(default=120, ge=1, le=3_600)
    media_ingest_max_attempts: int = Field(default=3, ge=1, le=10)
    media_max_duration_seconds: int = Field(default=3_600, ge=1, le=86_400)
    media_max_long_edge: int = Field(default=3_840, ge=1, le=16_384)
    media_max_short_edge: int = Field(default=2_160, ge=1, le=16_384)
    media_max_total_pixels: int = Field(default=8_294_400, ge=1, le=268_435_456)
    media_proxy_max_long_edge: int = Field(default=1_280, ge=2, le=16_384)
    media_proxy_max_short_edge: int = Field(default=720, ge=2, le=16_384)
    media_thumbnail_max_long_edge: int = Field(default=640, ge=2, le=16_384)
    media_thumbnail_max_short_edge: int = Field(default=640, ge=2, le=16_384)
    media_max_derivative_bytes: int = Field(default=52_428_800, ge=1, le=2_147_483_647)
    scene_min_duration_ms: int = Field(default=500, ge=1, le=60_000)
    scene_max_count: int = Field(default=500, ge=1, le=10_000)
    audio_extraction_timeout_seconds: int = Field(default=120, ge=1, le=3_600)
    media_max_extracted_audio_bytes: int = Field(default=115_200_044, ge=1, le=2_147_483_647)
    asr_timeout_seconds: int = Field(default=120, ge=1, le=3_600)
    transcript_max_segment_count: int = Field(default=2_000, ge=1, le=20_000)
    transcript_max_segment_chars: int = Field(default=4_000, ge=1, le=4_000)
    transcript_max_total_chars: int = Field(default=1_000_000, ge=1, le=8_000_000)
    transcript_min_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    video_understanding_max_summary_chars: int = Field(default=1_000, ge=1, le=20_000)
    video_understanding_max_visual_description_chars: int = Field(default=2_000, ge=1, le=20_000)
    video_understanding_max_transcript_context_chars: int = Field(default=4_000, ge=1, le=20_000)
    video_understanding_max_labels: int = Field(default=20, ge=1, le=200)
    video_understanding_max_objects: int = Field(default=30, ge=1, le=200)
    video_understanding_max_actions: int = Field(default=20, ge=1, le=200)
    video_understanding_max_visible_text_items: int = Field(default=20, ge=1, le=200)
    video_understanding_max_visible_text_item_chars: int = Field(default=500, ge=1, le=4_000)
    video_understanding_max_json_bytes: int = Field(default=32_768, ge=256, le=1_048_576)
    video_understanding_max_json_depth: int = Field(default=5, ge=1, le=20)
    video_understanding_min_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    frame_extraction_timeout_seconds: int = Field(default=30, ge=1, le=3_600)
    video_understanding_timeout_seconds: int = Field(default=60, ge=1, le=3_600)
    video_understanding_job_timeout_seconds: int = Field(default=120, ge=1, le=3_600)
    video_understanding_max_attempts: int = Field(default=3, ge=1, le=10)
    ffmpeg_binary: str = Field(default="/usr/bin/ffmpeg", min_length=1, max_length=512)
    ffprobe_binary: str = Field(default="/usr/bin/ffprobe", min_length=1, max_length=512)
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
    # Deprecated environment compatibility only. New deployments must use the
    # orientation-independent long/short edge settings above.
    media_max_width: int | None = Field(default=None, ge=1, le=16_384)
    media_max_height: int | None = Field(default=None, ge=1, le=16_384)

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

    @field_validator("ffmpeg_binary", "ffprobe_binary")
    @classmethod
    def validate_media_binary(cls, value: str) -> str:
        if not value.startswith("/") or "\x00" in value:
            raise ValueError("media binary paths must be absolute paths")
        return value

    @model_validator(mode="after")
    def normalize_legacy_media_dimensions(self) -> Settings:
        """Map the former width/height pair to orientation-independent limits."""

        legacy_fields = {"media_max_width", "media_max_height"}
        current_fields = {"media_max_long_edge", "media_max_short_edge"}
        has_legacy = bool(self.model_fields_set & legacy_fields)
        has_current = bool(self.model_fields_set & current_fields)
        if has_legacy and has_current:
            raise ValueError(
                "MEDIA_MAX_WIDTH/MEDIA_MAX_HEIGHT cannot be combined with "
                "MEDIA_MAX_LONG_EDGE/MEDIA_MAX_SHORT_EDGE"
            )
        if has_legacy:
            if self.media_max_width is None or self.media_max_height is None:
                raise ValueError("MEDIA_MAX_WIDTH and MEDIA_MAX_HEIGHT must be provided together")
            self.media_max_long_edge = max(self.media_max_width, self.media_max_height)
            self.media_max_short_edge = min(self.media_max_width, self.media_max_height)
        if self.media_max_short_edge > self.media_max_long_edge:
            raise ValueError("MEDIA_MAX_SHORT_EDGE cannot exceed MEDIA_MAX_LONG_EDGE")
        if self.media_proxy_max_short_edge > self.media_proxy_max_long_edge:
            raise ValueError("MEDIA_PROXY_MAX_SHORT_EDGE cannot exceed MEDIA_PROXY_MAX_LONG_EDGE")
        if self.media_thumbnail_max_short_edge > self.media_thumbnail_max_long_edge:
            raise ValueError(
                "MEDIA_THUMBNAIL_MAX_SHORT_EDGE cannot exceed MEDIA_THUMBNAIL_MAX_LONG_EDGE"
            )
        required_audio_bytes = (
            self.media_max_duration_seconds
            * PCM_AUDIO_SAMPLE_RATE_HZ
            * PCM_AUDIO_CHANNELS
            * PCM_AUDIO_BYTES_PER_SAMPLE
            + PCM_WAV_HEADER_BYTES
        )
        if self.media_max_extracted_audio_bytes < required_audio_bytes:
            raise ValueError(
                "MEDIA_MAX_EXTRACTED_AUDIO_BYTES must cover the maximum PCM WAV duration"
            )
        if self.transcript_max_total_chars < self.transcript_max_segment_chars:
            raise ValueError("TRANSCRIPT_MAX_TOTAL_CHARS cannot be below segment capacity")
        if self.transcript_max_total_chars > (
            self.transcript_max_segment_count * self.transcript_max_segment_chars
        ):
            raise ValueError("TRANSCRIPT_MAX_TOTAL_CHARS exceeds configured segment capacity")
        if self.video_understanding_job_timeout_seconds < (
            self.frame_extraction_timeout_seconds + self.video_understanding_timeout_seconds
        ):
            raise ValueError(
                "VIDEO_UNDERSTANDING_JOB_TIMEOUT_SECONDS cannot be below the combined "
                "frame extraction and provider timeouts"
            )
        return self

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
