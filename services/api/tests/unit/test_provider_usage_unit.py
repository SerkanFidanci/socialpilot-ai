"""Unit coverage for the provider_usage persistence shape (W10, item 1).

These need no database: they introspect the ORM model and exercise ``from_measurement`` in
memory. The point is that ``operations.ProviderUsage`` adopts the benchmark's
``ProviderUsageRecord`` shape (no second cost model) while structurally excluding tokens,
prompts, signed URLs and raw responses.
"""

from __future__ import annotations

from uuid import uuid4

from app.benchmark.model import Capability, ProviderUsageRecord
from app.modules.operations.models import ProviderUsage

# Substrings ADR-007 keeps out of a usage record. None may appear as a column name.
_FORBIDDEN_COLUMN_SUBSTRINGS = (
    "token",
    "prompt",
    "url",
    "response",
    "signature",
    "payload",
    "secret",
)


def test_provider_usage_excludes_sensitive_columns() -> None:
    columns = set(ProviderUsage.__table__.columns.keys())
    for forbidden in _FORBIDDEN_COLUMN_SUBSTRINGS:
        assert not any(forbidden in name for name in columns), (forbidden, columns)


def test_provider_usage_columns_are_exactly_the_adr_007_shape() -> None:
    assert set(ProviderUsage.__table__.columns.keys()) == {
        "id",
        "business_id",
        "job_id",
        "asset_id",
        "run_id",
        "capability",
        "provider",
        "model",
        "estimated_cost_minor",
        "actual_cost_minor",
        "currency",
        "duration_ms",
        "outcome",
        "correlation_id",
        "created_at",
    }


def test_from_measurement_maps_a_benchmark_record_field_for_field() -> None:
    record = ProviderUsageRecord(
        capability=Capability.ASR,
        provider="fake-asr",
        model="fake-asr-1",
        estimated_cost_minor=10,
        actual_cost_minor=12,
        currency="USD",
        duration_ms=34,
        outcome="success",
        correlation_id="bench-asr-1",
        route_revision="route-1",
        prompt_version="prompt-1",
        data_region="eu",
    )
    business_id, job_id, asset_id = uuid4(), uuid4(), uuid4()

    row = ProviderUsage.from_measurement(
        business_id=business_id,
        capability=record.capability.value,
        provider=record.provider,
        model=record.model,
        estimated_cost_minor=record.estimated_cost_minor,
        actual_cost_minor=record.actual_cost_minor,
        currency=record.currency,
        duration_ms=record.duration_ms,
        outcome=record.outcome,
        correlation_id=record.correlation_id,
        job_id=job_id,
        asset_id=asset_id,
        run_id="run-1",
    )

    assert row.business_id == business_id
    assert row.job_id == job_id
    assert row.asset_id == asset_id
    assert row.run_id == "run-1"
    assert row.capability == "asr"
    assert row.provider == "fake-asr"
    assert row.model == "fake-asr-1"
    assert row.estimated_cost_minor == 10
    assert row.actual_cost_minor == 12
    assert row.currency == "USD"
    assert row.duration_ms == 34
    assert row.outcome == "success"
    assert row.correlation_id == "bench-asr-1"


def test_from_measurement_defaults_context_to_null_for_an_offline_measurement() -> None:
    row = ProviderUsage.from_measurement(
        business_id=uuid4(),
        capability=Capability.TTS.value,
        provider="fake-tts",
        model="fake-tts-1",
        estimated_cost_minor=0,
        actual_cost_minor=0,
        currency="USD",
        duration_ms=1,
        outcome="success",
        correlation_id="bench-tts-1",
    )

    assert row.job_id is None
    assert row.asset_id is None
    assert row.run_id is None
