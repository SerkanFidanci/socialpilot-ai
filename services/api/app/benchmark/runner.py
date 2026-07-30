"""Orchestrate the benchmark: invoke providers under a cost cap and score against ground truth.

Determinism: fake providers return the same output every run, so a metric value is
reproducible. Non-determinism is still handled — ``runs > 1`` re-invokes each provider (paying
each time) and the report carries the min/max/mean/stdev distribution, so a real provider is
never judged on a single sample.
"""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Mapping, Sequence

from app.benchmark import metrics
from app.benchmark.golden import CapabilitySpec, GoldenSample
from app.benchmark.model import (
    BenchmarkCostCapExceeded,
    BenchmarkReport,
    Capability,
    CapabilityReport,
    CostLedger,
    MetricSummary,
    ProviderInput,
    ProviderUsageRecord,
    require_minimized_input,
)
from app.benchmark.providers import BenchmarkProvider, ProviderRegistry
from app.core.config import Settings
from app.modules.media.video_understanding import (
    VideoUnderstandingPermanentError,
    normalize_provider_output,
)

# (unit, auto_scored, note) for every metric name the harness can emit.
_MANUAL_METRICS: dict[Capability, tuple[tuple[str, str, str], ...]] = {
    Capability.TEXT_STRATEGY: (
        (
            "brand_tone",
            "score",
            "Turkish brand tone is not auto-scored; requires human or LLM-judge review.",
        ),
    ),
    Capability.TTS: (
        (
            "prosody_pronunciation",
            "score",
            "Turkish prosody/pronunciation needs audio and perceptual review; not auto-scored.",
        ),
    ),
}


class _Accumulator:
    """Collects per-sample scores for one capability across every run."""

    def __init__(self) -> None:
        # metric name -> one aggregated scalar per run
        self.run_values: dict[str, list[float]] = defaultdict(list)
        self.invalid_rates: list[float] = []
        self.latencies_ms: list[int] = []
        self.cost_minor: int = 0
        self.sample_count: int = 0
        self.runs: int = 0
        self.prompt_versions: set[str] = set()
        self.route_revisions: set[str] = set()


def _bounds(segments: object) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    if isinstance(segments, list):
        for segment in segments:
            if isinstance(segment, dict):
                start, end = segment.get("start_ms"), segment.get("end_ms")
                if isinstance(start, int) and isinstance(end, int):
                    out.append((start, end))
    return out


def _joined_text(segments: object) -> str:
    parts: list[str] = []
    if isinstance(segments, list):
        for segment in segments:
            if isinstance(segment, dict):
                text = segment.get("text")
                if isinstance(text, str):
                    parts.append(text)
    return " ".join(parts)


def _str_list(value: object) -> tuple[str, ...]:
    if isinstance(value, list):
        return tuple(item for item in value if isinstance(item, str))
    return ()


def _durations(segments: object, key: str) -> list[int]:
    out: list[int] = []
    if isinstance(segments, list):
        for segment in segments:
            if isinstance(segment, dict):
                value = segment.get(key)
                if isinstance(value, int):
                    out.append(value)
    return out


def _score_asr(samples: Sequence[tuple[GoldenSample, Mapping[str, object]]]) -> dict[str, float]:
    clean_wer: list[float] = []
    noisy_wer: list[float] = []
    all_wer: list[float] = []
    drifts: list[float] = []
    brand_hits: list[float] = []
    for sample, output in samples:
        ground_truth = sample.capabilities[Capability.ASR].ground_truth
        reference_text = _joined_text(ground_truth.get("segments"))
        hypothesis_text = _joined_text(output.get("segments"))
        wer = metrics.word_error_rate(reference_text, hypothesis_text)
        all_wer.append(wer)
        (noisy_wer if "noisy" in sample.qualities else clean_wer).append(wer)
        drifts.append(
            metrics.timestamp_drift_ms(
                _bounds(ground_truth.get("segments")), _bounds(output.get("segments"))
            )
        )
        brand_hits.append(
            metrics.brand_term_hit_rate(hypothesis_text, _str_list(ground_truth.get("brand_terms")))
        )
    clean_mean = sum(clean_wer) / len(clean_wer) if clean_wer else 0.0
    noisy_mean = sum(noisy_wer) / len(noisy_wer) if noisy_wer else clean_mean
    return {
        "wer": sum(all_wer) / len(all_wer),
        "timestamp_drift_ms": sum(drifts) / len(drifts),
        "brand_term_hit_rate": sum(brand_hits) / len(brand_hits),
        "noisy_degradation_wer": noisy_mean - clean_mean,
    }


def _score_video(
    samples: Sequence[tuple[GoldenSample, Mapping[str, object]]], settings: Settings
) -> tuple[dict[str, float], float]:
    jaccard: list[float] = []
    detection: list[float] = []
    flags: list[float] = []
    invalid = 0
    for sample, output in samples:
        ground_truth = sample.capabilities[Capability.VIDEO_UNDERSTANDING].ground_truth
        jaccard.append(
            metrics.label_jaccard(
                _str_list(output.get("labels")), _str_list(ground_truth.get("scene_labels"))
            )
        )
        detection.append(
            metrics.detection_f1(
                _str_list(output.get("objects")), _str_list(ground_truth.get("objects"))
            )
        )
        flags.append(
            metrics.flags_exact_match(
                _str_list(output.get("safety_flags")), _str_list(ground_truth.get("safety_flags"))
            )
        )
        # Schema fidelity is measured with the real domain validator, not a parallel one.
        try:
            normalize_provider_output(output, settings)
        except VideoUnderstandingPermanentError:
            invalid += 1
    count = len(samples)
    scores = {
        "scene_label_jaccard": sum(jaccard) / count,
        "object_detection_f1": sum(detection) / count,
        "unsafe_flag_accuracy": sum(flags) / count,
    }
    return scores, invalid / count


def _score_text(samples: Sequence[tuple[GoldenSample, Mapping[str, object]]]) -> dict[str, float]:
    forbidden = 0
    fabricated = 0
    cta_ok: list[float] = []
    for sample, output in samples:
        ground_truth = sample.capabilities[Capability.TEXT_STRATEGY].ground_truth
        text = output.get("text")
        text = text if isinstance(text, str) else ""
        cta = output.get("cta")
        cta = cta if isinstance(cta, str) else ""
        allowed = ground_truth.get("allowed_facts")
        prices = _str_list(allowed.get("prices")) if isinstance(allowed, dict) else ()
        dates = _str_list(allowed.get("dates")) if isinstance(allowed, dict) else ()
        forbidden += metrics.count_forbidden_words(
            text, _str_list(ground_truth.get("forbidden_words"))
        )
        fabricated += metrics.count_fabricated_facts(text, prices, dates)
        cta_ok.append(
            1.0
            if metrics.cta_from_approved(cta, _str_list(ground_truth.get("approved_ctas")))
            else 0.0
        )
    return {
        "forbidden_word_violations": float(forbidden),
        "fabricated_fact_count": float(fabricated),
        "cta_from_approved_rate": sum(cta_ok) / len(cta_ok),
    }


def _score_timeline(
    samples: Sequence[tuple[GoldenSample, Mapping[str, object]]],
) -> dict[str, float]:
    conforms: list[float] = []
    boundary: list[float] = []
    for sample, output in samples:
        ground_truth = sample.capabilities[Capability.STRUCTURED_TIMELINE].ground_truth
        duration = ground_truth.get("duration_ms")
        duration = duration if isinstance(duration, int) else 0
        result = 1.0 if metrics.timeline_conforms(output.get("timeline"), duration) else 0.0
        conforms.append(result)
        if "boundary" in sample.qualities:
            boundary.append(result)
    scores = {"schema_conformance_rate": sum(conforms) / len(conforms)}
    if boundary:
        scores["boundary_conformance_rate"] = sum(boundary) / len(boundary)
    return scores


def _score_tts(samples: Sequence[tuple[GoldenSample, Mapping[str, object]]]) -> dict[str, float]:
    deviations: list[float] = []
    coverage: list[float] = []
    for sample, output in samples:
        ground_truth = sample.capabilities[Capability.TTS].ground_truth
        gt_segments = ground_truth.get("segments")
        deviations.append(
            metrics.duration_deviation_ms(
                _durations(gt_segments, "expected_duration_ms"),
                _durations(output.get("segments"), "estimated_duration_ms"),
            )
        )
        if isinstance(gt_segments, list):
            for segment in gt_segments:
                if isinstance(segment, dict):
                    text = segment.get("text")
                    coverage.append(
                        metrics.turkish_phoneme_coverage(
                            text if isinstance(text, str) else "",
                            _str_list(segment.get("turkish_phonemes")),
                        )
                    )
    return {
        "segment_duration_deviation_ms": sum(deviations) / len(deviations),
        "turkish_phoneme_coverage": sum(coverage) / len(coverage) if coverage else 1.0,
    }


def _invoke(
    *,
    provider: BenchmarkProvider,
    capability: Capability,
    sample: GoldenSample,
    spec: CapabilitySpec,
    ledger: CostLedger,
    run_index: int,
) -> tuple[Mapping[str, object], int]:
    """Reserve budget, invoke, settle the usage record; returns (output, latency_ms)."""

    descriptor = provider.descriptor
    ledger.reserve(
        capability=capability, provider=descriptor.name, estimated_minor=descriptor.unit_cost_minor
    )
    provider_input = ProviderInput(proxy_reference=f"proxy://{sample.id}", is_original=False)
    require_minimized_input(provider_input)
    started = time.perf_counter_ns()
    output = provider.invoke(capability=capability, sample=sample, provider_input=provider_input)
    latency_ms = (time.perf_counter_ns() - started) // 1_000_000
    # The call itself returned; schema validity of the output is scored separately.
    outcome = "success"
    ledger.settle(
        ProviderUsageRecord(
            capability=capability,
            provider=descriptor.name,
            model=descriptor.model,
            estimated_cost_minor=descriptor.unit_cost_minor,
            actual_cost_minor=descriptor.unit_cost_minor,
            currency=descriptor.currency,
            duration_ms=int(latency_ms),
            outcome=outcome,
            correlation_id=f"bench-{capability.value}-{sample.id}-r{run_index}",
            route_revision=spec.route_revision,
            prompt_version=spec.prompt_version,
            data_region=descriptor.data_region,
        )
    )
    return output, int(latency_ms)


def _aggregate_run(
    capability: Capability,
    provider: BenchmarkProvider,
    samples: Sequence[GoldenSample],
    ledger: CostLedger,
    settings: Settings,
    run_index: int,
    accumulator: _Accumulator,
) -> None:
    outputs: list[tuple[GoldenSample, Mapping[str, object]]] = []
    for sample in samples:
        spec = sample.capabilities[capability]
        accumulator.prompt_versions.add(spec.prompt_version)
        accumulator.route_revisions.add(spec.route_revision)
        output, latency_ms = _invoke(
            provider=provider,
            capability=capability,
            sample=sample,
            spec=spec,
            ledger=ledger,
            run_index=run_index,
        )
        accumulator.latencies_ms.append(latency_ms)
        accumulator.cost_minor += provider.descriptor.unit_cost_minor
        outputs.append((sample, output))

    invalid_rate = 0.0
    if capability is Capability.ASR:
        scores = _score_asr(outputs)
    elif capability is Capability.VIDEO_UNDERSTANDING:
        scores, invalid_rate = _score_video(outputs, settings)
    elif capability is Capability.TEXT_STRATEGY:
        scores = _score_text(outputs)
    elif capability is Capability.STRUCTURED_TIMELINE:
        scores = _score_timeline(outputs)
    else:
        scores = _score_tts(outputs)

    for name, value in scores.items():
        accumulator.run_values[name].append(value)
    accumulator.invalid_rates.append(invalid_rate)
    accumulator.sample_count = len(samples)
    accumulator.runs += 1


def _build_capability_report(
    capability: Capability, provider: BenchmarkProvider, accumulator: _Accumulator
) -> CapabilityReport:
    metric_summaries: list[MetricSummary] = []
    for name, values in accumulator.run_values.items():
        mean, minimum, maximum, stdev = metrics.distribution(values)
        metric_summaries.append(
            MetricSummary(
                name=name,
                unit=_UNITS.get(name, "ratio"),
                auto_scored=True,
                mean=mean,
                minimum=minimum,
                maximum=maximum,
                stdev=stdev,
            )
        )
    for name, unit, note in _MANUAL_METRICS.get(capability, ()):
        metric_summaries.append(
            MetricSummary(
                name=name,
                unit=unit,
                auto_scored=False,
                mean=None,
                minimum=None,
                maximum=None,
                stdev=None,
                note=note,
            )
        )
    descriptor = provider.descriptor
    invalid = (
        sum(accumulator.invalid_rates) / len(accumulator.invalid_rates)
        if accumulator.invalid_rates
        else 0.0
    )
    latency = (
        sum(accumulator.latencies_ms) / len(accumulator.latencies_ms)
        if accumulator.latencies_ms
        else 0.0
    )
    return CapabilityReport(
        capability=capability,
        provider=descriptor.name,
        model=descriptor.model,
        data_region=descriptor.data_region,
        face_voice_input_allowed=descriptor.face_voice_input_allowed,
        compliance_note=descriptor.compliance_note,
        prompt_versions=tuple(sorted(accumulator.prompt_versions)),
        route_revisions=tuple(sorted(accumulator.route_revisions)),
        sample_count=accumulator.sample_count,
        runs=accumulator.runs,
        invalid_output_rate=invalid,
        total_cost_minor=accumulator.cost_minor,
        mean_latency_ms=latency,
        metrics=tuple(sorted(metric_summaries, key=lambda summary: summary.name)),
    )


_UNITS: dict[str, str] = {
    "wer": "ratio",
    "timestamp_drift_ms": "ms",
    "brand_term_hit_rate": "ratio",
    "noisy_degradation_wer": "ratio",
    "scene_label_jaccard": "ratio",
    "object_detection_f1": "ratio",
    "unsafe_flag_accuracy": "ratio",
    "forbidden_word_violations": "count",
    "fabricated_fact_count": "count",
    "cta_from_approved_rate": "ratio",
    "schema_conformance_rate": "ratio",
    "boundary_conformance_rate": "ratio",
    "segment_duration_deviation_ms": "ms",
    "turkish_phoneme_coverage": "ratio",
}


def run_benchmark(
    *,
    registry: ProviderRegistry,
    samples: Sequence[GoldenSample],
    settings: Settings,
    runs: int = 1,
    cost_cap_minor: int | None = None,
    currency: str = "USD",
) -> BenchmarkReport:
    if runs < 1:
        raise ValueError("runs must be at least 1")
    ledger = CostLedger(cap_minor=cost_cap_minor)
    accumulators: dict[Capability, _Accumulator] = {}
    halted = False
    halt_reason: str | None = None

    try:
        for run_index in range(runs):
            for capability in registry.capabilities():
                capability_samples = [sample for sample in samples if sample.has(capability)]
                if not capability_samples:
                    continue
                accumulator = accumulators.setdefault(capability, _Accumulator())
                _aggregate_run(
                    capability,
                    registry.provider_for(capability),
                    capability_samples,
                    ledger,
                    settings,
                    run_index,
                    accumulator,
                )
    except BenchmarkCostCapExceeded as exceeded:
        halted = True
        halt_reason = str(exceeded)

    capability_reports = tuple(
        _build_capability_report(capability, registry.provider_for(capability), accumulator)
        for capability, accumulator in sorted(accumulators.items(), key=lambda item: item[0].value)
        if accumulator.runs > 0
    )
    return BenchmarkReport(
        provider_set=registry.name,
        runs=runs,
        cost_cap_minor=cost_cap_minor,
        total_cost_minor=ledger.spent_minor,
        currency=currency,
        halted=halted,
        halt_reason=halt_reason,
        capabilities=capability_reports,
        usage=tuple(ledger.records),
    )
