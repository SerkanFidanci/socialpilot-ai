"""Neutral value objects shared across the provider benchmark harness.

This module owns the *shape* of a provider-usage record and the cost ledger. It deliberately
mirrors the fields ADR-007 assigns to ``provider_usage`` (capability, provider/model,
estimated and actual integer-minor-unit cost, currency, duration, outcome, correlation id)
plus route/prompt provenance, so that when a persistence layer for provider usage is built it
can adopt this record instead of the harness growing a second cost model. Nothing here
persists to a database; the harness is an offline measurement tool.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Capability(StrEnum):
    """The five capabilities PRD §40.5 / W08 require a metric for."""

    ASR = "asr"
    VIDEO_UNDERSTANDING = "video_understanding"
    TEXT_STRATEGY = "text_strategy"
    STRUCTURED_TIMELINE = "structured_timeline"
    TTS = "tts"


class BenchmarkError(RuntimeError):
    """Base class for harness-level failures."""


class BenchmarkCostCapExceeded(BenchmarkError):
    """Raised before an invocation whose estimated cost would breach the cap.

    The message never contains provider secrets; it carries only accounting integers so a
    halted run is auditable.
    """

    def __init__(
        self,
        *,
        capability: Capability,
        provider: str,
        spent_minor: int,
        estimated_minor: int,
        cap_minor: int,
    ) -> None:
        self.capability = capability
        self.provider = provider
        self.spent_minor = spent_minor
        self.estimated_minor = estimated_minor
        self.cap_minor = cap_minor
        super().__init__(
            f"cost cap {cap_minor} minor units would be exceeded: already spent {spent_minor}, "
            f"next {capability.value} call on {provider} estimated {estimated_minor}"
        )


class BenchmarkProvenanceError(BenchmarkError):
    """Raised when a sample lacks the prompt version or route revision (PRD §17.6).

    A measurement whose prompt version is unknown cannot be used, so the harness refuses to
    score it rather than emitting an unattributable number.
    """


class BenchmarkDataMinimizationError(BenchmarkError):
    """Raised when an original (non-proxy) input would reach a provider (§34.3)."""


@dataclass(frozen=True)
class ProviderDescriptor:
    """Static facts about a provider needed for cost, routing and legal eligibility.

    ``face_voice_input_allowed`` and ``compliance_note`` exist because, under KVKK
    cross-border transfer rules, a provider outside Turkey without a standard contract cannot
    lawfully receive face/voice-bearing media even if it scores best.
    """

    name: str
    model: str
    data_region: str
    face_voice_input_allowed: bool
    unit_cost_minor: int
    currency: str
    compliance_note: str


@dataclass(frozen=True)
class ProviderInput:
    """What actually reaches a provider: a minimized proxy/scene reference, never the original."""

    proxy_reference: str
    is_original: bool = False


def require_minimized_input(provider_input: ProviderInput) -> None:
    """Enforce §34.3 data minimization at the harness boundary."""

    if provider_input.is_original:
        raise BenchmarkDataMinimizationError(
            "the benchmark must send a proxy/scene reference to a provider, never the original"
        )


@dataclass(frozen=True)
class ProviderUsageRecord:
    """One attributable provider call. Mirrors ADR-007 ``provider_usage`` fields.

    Excludes, by construction, the things ADR-007 keeps out of usage records: tokens, prompts,
    signed URLs and full provider payloads.
    """

    capability: Capability
    provider: str
    model: str
    estimated_cost_minor: int
    actual_cost_minor: int
    currency: str
    duration_ms: int
    outcome: str
    correlation_id: str
    route_revision: str
    prompt_version: str
    data_region: str


@dataclass
class CostLedger:
    """Enforces the cost cap deterministically.

    ``reserve`` is called *before* a provider is invoked, using the estimated cost, so the run
    halts without spending when the next call would breach the cap — it never silently spends
    past the ceiling. ``settle`` records the actual cost after the call returns.
    """

    cap_minor: int | None
    spent_minor: int = 0
    records: list[ProviderUsageRecord] = field(default_factory=list)

    def reserve(self, *, capability: Capability, provider: str, estimated_minor: int) -> None:
        if self.cap_minor is not None and self.spent_minor + estimated_minor > self.cap_minor:
            raise BenchmarkCostCapExceeded(
                capability=capability,
                provider=provider,
                spent_minor=self.spent_minor,
                estimated_minor=estimated_minor,
                cap_minor=self.cap_minor,
            )

    def settle(self, record: ProviderUsageRecord) -> None:
        self.spent_minor += record.actual_cost_minor
        self.records.append(record)


@dataclass(frozen=True)
class MetricSummary:
    """A single metric aggregated across the runs of one capability.

    ``auto_scored`` is False for dimensions a machine cannot judge on its own (brand tone,
    prosody); the harness reports those honestly instead of inventing a number.
    """

    name: str
    unit: str
    auto_scored: bool
    mean: float | None
    minimum: float | None
    maximum: float | None
    stdev: float | None
    note: str = ""


@dataclass(frozen=True)
class CapabilityReport:
    capability: Capability
    provider: str
    model: str
    data_region: str
    face_voice_input_allowed: bool
    compliance_note: str
    prompt_versions: tuple[str, ...]
    route_revisions: tuple[str, ...]
    sample_count: int
    runs: int
    invalid_output_rate: float
    total_cost_minor: int
    mean_latency_ms: float
    metrics: tuple[MetricSummary, ...]


@dataclass(frozen=True)
class BenchmarkReport:
    provider_set: str
    runs: int
    cost_cap_minor: int | None
    total_cost_minor: int
    currency: str
    halted: bool
    halt_reason: str | None
    capabilities: tuple[CapabilityReport, ...]
    usage: tuple[ProviderUsageRecord, ...]
