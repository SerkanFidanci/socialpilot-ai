"""Unit coverage for pure operational reliability rules."""

from __future__ import annotations

from app.modules.operations.models import JobStatus, OutboxStatus
from app.modules.operations.service import JobStateService, request_fingerprint


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
