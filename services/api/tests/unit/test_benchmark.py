"""Tests for the provider benchmark harness (W08).

These are the harness's own tests: they verify the tool computes real numbers against ground
truth, halts on the cost cap, records provenance/data-region, and never sends an original.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.benchmark import (
    build_registry,
    load_samples,
    metrics,
    run_benchmark,
    to_json_dict,
    to_markdown,
)
from app.benchmark.golden import load_samples as load_samples_from
from app.benchmark.model import (
    BenchmarkDataMinimizationError,
    BenchmarkProvenanceError,
    BenchmarkReport,
    Capability,
    ProviderInput,
    require_minimized_input,
)
from app.benchmark.providers import build_fake_registry
from app.core.config import Settings, get_settings
from scripts import make_golden_media
from scripts import run_benchmark as run_benchmark_cli

# Every quality PRD §40.5 lists must appear somewhere in the committed golden set.
_REQUIRED_QUALITIES = {
    "vertical",
    "horizontal",
    "noisy",
    "turkish_speech",
    "multi_product",
    "dark",
    "shaky",
    "human_face",
    "before_after",
    "logo",
    "small_text",
}


@pytest.fixture(scope="module")
def settings() -> Settings:
    return get_settings()


def _report(
    settings: Settings, *, runs: int = 1, cost_cap_minor: int | None = None
) -> BenchmarkReport:
    return run_benchmark(
        registry=build_fake_registry(),
        samples=load_samples(),
        settings=settings,
        runs=runs,
        cost_cap_minor=cost_cap_minor,
    )


def test_golden_set_covers_every_required_quality() -> None:
    covered: set[str] = set()
    for sample in load_samples():
        covered.update(sample.qualities)
    assert _REQUIRED_QUALITIES <= covered


def test_all_five_capabilities_scored(settings: Settings) -> None:
    report = _report(settings)
    scored = {capability.capability for capability in report.capabilities}
    assert scored == set(Capability)
    for capability in report.capabilities:
        auto = [metric for metric in capability.metrics if metric.auto_scored]
        assert auto, f"{capability.capability} has no auto-scored metric"
        assert all(metric.mean is not None for metric in auto)


def _metric(report: BenchmarkReport, capability: Capability, name: str) -> float:
    entry = next(c for c in report.capabilities if c.capability is capability)
    summary = next(m for m in entry.metrics if m.name == name)
    assert summary.mean is not None
    return summary.mean


def test_asr_metrics_match_ground_truth(settings: Settings) -> None:
    report = _report(settings)
    assert _metric(report, Capability.ASR, "wer") == pytest.approx((0.1 + 2 / 11) / 2)
    assert _metric(report, Capability.ASR, "timestamp_drift_ms") == pytest.approx(31.25)
    assert _metric(report, Capability.ASR, "brand_term_hit_rate") == pytest.approx(0.5)
    assert _metric(report, Capability.ASR, "noisy_degradation_wer") == pytest.approx(2 / 11 - 0.1)


def test_video_metrics_and_schema_fidelity(settings: Settings) -> None:
    report = _report(settings)
    entry = next(c for c in report.capabilities if c.capability is Capability.VIDEO_UNDERSTANDING)
    # One of three fake outputs is schema-invalid (confidence 2.0), measured by the real validator.
    assert entry.invalid_output_rate == pytest.approx(1 / 3)
    assert _metric(report, Capability.VIDEO_UNDERSTANDING, "unsafe_flag_accuracy") == pytest.approx(
        1.0
    )
    assert _metric(report, Capability.VIDEO_UNDERSTANDING, "scene_label_jaccard") == pytest.approx(
        (2 / 3 + 1.0 + 0.75) / 3
    )


def test_text_strategy_counts_violations(settings: Settings) -> None:
    report = _report(settings)
    assert _metric(report, Capability.TEXT_STRATEGY, "forbidden_word_violations") == pytest.approx(
        1.0
    )
    assert _metric(report, Capability.TEXT_STRATEGY, "fabricated_fact_count") == pytest.approx(2.0)
    assert _metric(report, Capability.TEXT_STRATEGY, "cta_from_approved_rate") == pytest.approx(1.0)


def test_timeline_conformance_and_boundary(settings: Settings) -> None:
    report = _report(settings)
    assert _metric(
        report, Capability.STRUCTURED_TIMELINE, "schema_conformance_rate"
    ) == pytest.approx(0.5)
    assert _metric(
        report, Capability.STRUCTURED_TIMELINE, "boundary_conformance_rate"
    ) == pytest.approx(0.0)


def test_tts_metrics(settings: Settings) -> None:
    report = _report(settings)
    assert _metric(report, Capability.TTS, "segment_duration_deviation_ms") == pytest.approx(150.0)
    assert _metric(report, Capability.TTS, "turkish_phoneme_coverage") == pytest.approx(1.0)


def test_brand_tone_and_prosody_are_not_auto_scored(settings: Settings) -> None:
    report = _report(settings)
    text = next(c for c in report.capabilities if c.capability is Capability.TEXT_STRATEGY)
    tone = next(m for m in text.metrics if m.name == "brand_tone")
    assert tone.auto_scored is False and tone.mean is None and tone.note
    tts = next(c for c in report.capabilities if c.capability is Capability.TTS)
    prosody = next(m for m in tts.metrics if m.name == "prosody_pronunciation")
    assert prosody.auto_scored is False and prosody.mean is None


def test_cost_recorded_through_single_usage_record(settings: Settings) -> None:
    report = _report(settings)
    # asr 2x2 + timeline 2x1 + text 2x1 + tts 1x2 + video 3x3 = 4+2+2+2+9
    assert report.total_cost_minor == 19
    assert len(report.usage) == sum(c.sample_count for c in report.capabilities)
    for record in report.usage:
        assert record.prompt_version and record.route_revision
        assert record.data_region


def test_cost_cap_halts_before_spending_over(settings: Settings) -> None:
    report = _report(settings, cost_cap_minor=10)
    assert report.halted is True
    assert report.halt_reason is not None
    assert report.total_cost_minor <= 10
    scored = {c.capability for c in report.capabilities}
    # video_understanding is processed last and its first call would breach the cap.
    assert Capability.VIDEO_UNDERSTANDING not in scored


def test_report_exposes_data_region_and_eligibility(settings: Settings) -> None:
    report = _report(settings)
    video = next(c for c in report.capabilities if c.capability is Capability.VIDEO_UNDERSTANDING)
    assert video.data_region
    assert video.face_voice_input_allowed is False
    # The machine-readable form carries the same fields.
    payload = to_json_dict(report)
    capabilities = payload["capabilities"]
    assert isinstance(capabilities, list)
    video_dict = next(c for c in capabilities if c["capability"] == "video_understanding")
    assert video_dict["data_region"] and video_dict["face_voice_input_allowed"] is False
    markdown = to_markdown(report)
    assert "Data region" in markdown
    assert "Face/voice OK" in markdown
    assert "Interpretation" in markdown


def test_data_minimization_guard() -> None:
    require_minimized_input(ProviderInput(proxy_reference="proxy://x"))
    with pytest.raises(BenchmarkDataMinimizationError):
        require_minimized_input(ProviderInput(proxy_reference="original://x", is_original=True))


def test_missing_prompt_version_is_refused(tmp_path: Path) -> None:
    (tmp_path / "bad.json").write_text(
        json.dumps(
            {
                "id": "bad",
                "kind": "text",
                "qualities": [],
                "media": None,
                "capabilities": {
                    "text_strategy": {
                        "route_revision": "r1",
                        "ground_truth": {
                            "forbidden_words": [],
                            "approved_ctas": [],
                            "allowed_facts": {},
                        },
                        "fake_output": {"text": "x", "cta": "y"},
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(BenchmarkProvenanceError):
        load_samples_from(tmp_path)


def test_multiple_runs_report_stable_distribution(settings: Settings) -> None:
    report = _report(settings, runs=3)
    assert report.total_cost_minor == 19 * 3
    for capability in report.capabilities:
        assert capability.runs == 3
        for metric in capability.metrics:
            if metric.auto_scored:
                assert metric.stdev == pytest.approx(0.0)
                assert metric.minimum == pytest.approx(metric.maximum)


def test_real_provider_set_is_configuration_surface_only() -> None:
    with pytest.raises(Exception) as excinfo:
        build_registry("real")
    assert "not wired" in str(excinfo.value)


def test_metric_functions_are_pure() -> None:
    assert metrics.word_error_rate("a b c", "a x c") == pytest.approx(1 / 3)
    assert metrics.word_error_rate("", "") == 0.0
    assert metrics.count_forbidden_words("garanti taze", ["garanti"]) == 1
    assert metrics.count_fabricated_facts("%15 ve %50 firsat", ["%15"], []) == 1
    assert metrics.timeline_conforms(
        {"segments": [{"index": 0, "start_ms": 0, "end_ms": 10, "text": "x"}]}, 10
    )
    assert not metrics.timeline_conforms(
        {"segments": [{"index": 0, "start_ms": 0, "end_ms": 20, "text": "x"}]}, 10
    )


def test_cli_writes_results_and_exit_codes(tmp_path: Path) -> None:
    out = tmp_path / "results.json"
    assert run_benchmark_cli.main(["--out", str(out)]) == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert {c["capability"] for c in payload["capabilities"]} == {c.value for c in Capability}
    assert run_benchmark_cli.main(["--cost-cap-minor", "10"]) == 2


def test_make_golden_media_is_deterministic_and_skips_non_media() -> None:
    samples = load_samples()
    by_id = {sample.id: sample for sample in samples}
    command = make_golden_media.build_ffmpeg_command(by_id["vertical_cafe_tr"], Path("out"))
    assert command is not None
    assert command[0] == "ffmpeg"
    assert command[-1].endswith("vertical_cafe_tr.mp4")
    # Running twice yields an identical command (no random seed, no clock).
    again = make_golden_media.build_ffmpeg_command(by_id["vertical_cafe_tr"], Path("out"))
    assert command == again
    # A text-only sample produces no media command.
    assert (
        make_golden_media.build_ffmpeg_command(by_id["strategy_clean_offer"], Path("out")) is None
    )
    plans = make_golden_media.plan_commands(samples, Path("out"))
    assert {sample_id for sample_id, _ in plans} == {
        "vertical_cafe_tr",
        "horizontal_product_demo",
        "dark_noisy_street_tr",
    }
