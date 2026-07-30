"""Settings validation tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def valid_values() -> dict[str, object]:
    return {
        "database_url": "postgresql+asyncpg://user:password@localhost:5432/socialpilot",
        "redis_url": "redis://localhost:6379/0",
        "celery_broker_url": "redis://localhost:6379/1",
        "celery_result_backend": "redis://localhost:6379/2",
    }


def test_settings_load_valid_groups() -> None:
    settings = Settings.model_validate(valid_values())

    assert settings.service_name == "socialpilot-api"
    assert settings.database_pool_size == 5
    assert settings.celery_result_backend.endswith("/2")


@pytest.mark.parametrize(
    ("override", "expected_field"),
    [
        ({"database_url": "postgresql://localhost/socialpilot"}, "database_url"),
        ({"postgres_port": 70000}, "postgres_port"),
        ({"app_env": "invalid"}, "app_env"),
    ],
)
def test_settings_reject_invalid_values(override: dict[str, object], expected_field: str) -> None:
    values = valid_values() | override

    with pytest.raises(ValidationError) as exception_info:
        Settings.model_validate(values)

    assert expected_field in str(exception_info.value)


def test_settings_reject_missing_required_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    values = valid_values()
    values.pop("database_url")

    with pytest.raises(ValidationError):
        Settings.model_validate(values)


def test_settings_reject_local_only_database_credential_in_production() -> None:
    values = valid_values() | {
        "app_env": "production",
        "database_url": "postgresql+asyncpg://socialpilot:local_only@localhost:5432/socialpilot",
    }

    with pytest.raises(ValidationError):
        Settings.model_validate(values)


@pytest.mark.parametrize(
    "override",
    [
        {"celery_task_soft_time_limit_seconds": 960},
        {"celery_task_timeout_seconds": 914},
    ],
)
def test_settings_enforce_celery_and_job_timeout_invariants(override: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate(valid_values() | override)


def test_settings_enforce_media_step_job_timeout_invariants() -> None:
    for override in (
        {"media_technical_job_timeout_seconds": 314},
        {"scene_speech_job_timeout_seconds": 314},
        {"celery_task_soft_time_limit_seconds": 899},
    ):
        with pytest.raises(ValidationError):
            Settings.model_validate(valid_values() | override)


def test_settings_calculate_supported_video_understanding_scene_limit() -> None:
    settings = Settings.model_validate(valid_values())
    assert settings.video_understanding_supported_scene_count == 5


def s3_values() -> dict[str, object]:
    # Pinned explicitly: the development container exports S3_* variables, and settings
    # tests must not depend on whatever the surrounding environment happens to hold.
    return valid_values() | {
        "storage_adapter": "s3",
        "s3_endpoint_url": "http://storage.internal:9000",
        "s3_presign_endpoint_url": "",
        "s3_bucket": "socialpilot-media",
        "s3_access_key_id": "access-key",
        "s3_secret_access_key": "secret-key",
    }


def test_settings_admit_the_ios_default_media_types() -> None:
    allowed = Settings.model_validate(valid_values()).media_allowed_mime_types

    assert {"image/heic", "image/heif", "video/quicktime"} <= set(allowed)


def test_settings_default_to_the_fake_storage_adapter() -> None:
    assert Settings.model_validate(valid_values()).storage_adapter == "fake"


def test_settings_reject_the_fake_storage_adapter_in_production() -> None:
    values = s3_values() | {"app_env": "production", "storage_adapter": "fake"}

    with pytest.raises(ValidationError, match="fake storage adapter") as exception_info:
        Settings.model_validate(values)

    # Both development-only adapters are reported together, not one restart at a time.
    assert "local identity adapter" in str(exception_info.value)


@pytest.mark.parametrize(
    "missing",
    ["s3_endpoint_url", "s3_bucket", "s3_access_key_id", "s3_secret_access_key"],
)
def test_settings_reject_incomplete_s3_configuration(missing: str) -> None:
    with pytest.raises(ValidationError, match="s3 storage adapter requires"):
        Settings.model_validate(s3_values() | {missing: ""})


@pytest.mark.parametrize(
    "endpoint",
    [
        "storage.internal:9000",
        "ftp://storage.internal",
        "http://storage.internal:9000/bucket",
        "http://storage.internal?x=1",
        "http://user:pass@storage.internal",
    ],
)
def test_settings_reject_unusable_s3_endpoints(endpoint: str) -> None:
    with pytest.raises(ValidationError, match="S3 endpoint"):
        Settings.model_validate(s3_values() | {"s3_endpoint_url": endpoint})


@pytest.mark.parametrize("bucket", ["Socialpilot-Media", "a", "bucket_name", "bucket/name"])
def test_settings_reject_unusable_s3_bucket_names(bucket: str) -> None:
    with pytest.raises(ValidationError, match="S3_BUCKET"):
        Settings.model_validate(s3_values() | {"s3_bucket": bucket})


def test_settings_presign_endpoint_defaults_to_the_server_endpoint() -> None:
    settings = Settings.model_validate(s3_values())

    assert settings.s3_presign_endpoint_url == "http://storage.internal:9000"


def test_settings_strip_a_trailing_slash_from_storage_endpoints() -> None:
    settings = Settings.model_validate(s3_values() | {"s3_endpoint_url": "http://storage:9000/"})

    assert settings.s3_endpoint_url == "http://storage:9000"
