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
