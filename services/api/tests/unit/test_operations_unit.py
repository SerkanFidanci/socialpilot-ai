"""Unit coverage for pure operational reliability rules."""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.modules.operations.models import JobStatus, OutboxStatus
from app.modules.operations.service import (
    JobStateService,
    calculate_video_understanding_job_timeout,
    request_fingerprint,
)


def settings(**changes: object) -> Settings:
    values: dict[str, object] = {
        "database_url": "postgresql+asyncpg://test:test@localhost:5432/test",
        "redis_url": "redis://localhost:6379/0",
        "celery_broker_url": "redis://localhost:6379/1",
        "celery_result_backend": "redis://localhost:6379/2",
    }
    values.update(changes)
    return Settings(**values)  # type: ignore[arg-type]


def test_request_fingerprint_is_canonical_and_payload_sensitive() -> None:
    first = request_fingerprint({"parts": [{"number": 1}], "checksum": "a" * 64})
    same = request_fingerprint({"checksum": "a" * 64, "parts": [{"number": 1}]})
    changed = request_fingerprint({"checksum": "b" * 64, "parts": [{"number": 1}]})
    assert first == same
    assert first != changed


def test_job_state_machine_rejects_terminal_and_skipping_transitions() -> None:
    assert JobStatus.SUCCEEDED not in JobStateService._allowed[JobStatus.QUEUED]
    assert JobStateService._allowed[JobStatus.SUCCEEDED] == frozenset()
    assert JobStatus.DEAD in JobStateService._allowed[JobStatus.FAILED]
    assert {status.value for status in OutboxStatus} == {
        "pending",
        "processing",
        "published",
        "failed",
        "dead",
    }


def test_video_understanding_timeout_scales_with_scene_count_and_rejects_overflow() -> None:
    resolved = settings()
    assert calculate_video_understanding_job_timeout(resolved, scene_count=1) == 120
    assert calculate_video_understanding_job_timeout(resolved, scene_count=2) == 210
    with pytest.raises(ValueError, match="VIDEO_UNDERSTANDING_JOB_TIMEOUT_EXCEEDED"):
        calculate_video_understanding_job_timeout(resolved, scene_count=10)
