"""Render a benchmark report as machine-readable JSON and a human comparison table.

The human table always carries the **data region** and **face/voice-input eligible** columns,
and states the interpretation rule up front: the best score is not the winner if the provider
cannot lawfully receive the input.
"""

from __future__ import annotations

from app.benchmark.model import BenchmarkReport, CapabilityReport, MetricSummary


def to_json_dict(report: BenchmarkReport) -> dict[str, object]:
    return {
        "provider_set": report.provider_set,
        "runs": report.runs,
        "cost_cap_minor": report.cost_cap_minor,
        "total_cost_minor": report.total_cost_minor,
        "currency": report.currency,
        "halted": report.halted,
        "halt_reason": report.halt_reason,
        "capabilities": [_capability_dict(capability) for capability in report.capabilities],
        "usage": [
            {
                "capability": record.capability.value,
                "provider": record.provider,
                "model": record.model,
                "estimated_cost_minor": record.estimated_cost_minor,
                "actual_cost_minor": record.actual_cost_minor,
                "currency": record.currency,
                "duration_ms": record.duration_ms,
                "outcome": record.outcome,
                "correlation_id": record.correlation_id,
                "route_revision": record.route_revision,
                "prompt_version": record.prompt_version,
                "data_region": record.data_region,
            }
            for record in report.usage
        ],
    }


def _capability_dict(capability: CapabilityReport) -> dict[str, object]:
    return {
        "capability": capability.capability.value,
        "provider": capability.provider,
        "model": capability.model,
        "data_region": capability.data_region,
        "face_voice_input_allowed": capability.face_voice_input_allowed,
        "compliance_note": capability.compliance_note,
        "prompt_versions": list(capability.prompt_versions),
        "route_revisions": list(capability.route_revisions),
        "sample_count": capability.sample_count,
        "runs": capability.runs,
        "invalid_output_rate": capability.invalid_output_rate,
        "total_cost_minor": capability.total_cost_minor,
        "mean_latency_ms": capability.mean_latency_ms,
        "metrics": [
            {
                "name": metric.name,
                "unit": metric.unit,
                "auto_scored": metric.auto_scored,
                "mean": metric.mean,
                "min": metric.minimum,
                "max": metric.maximum,
                "stdev": metric.stdev,
                "note": metric.note,
            }
            for metric in capability.metrics
        ],
    }


def _format_value(metric: MetricSummary) -> str:
    if not metric.auto_scored or metric.mean is None:
        return "manual"
    if metric.unit == "count":
        return f"{metric.mean:.0f}"
    return f"{metric.mean:.4f}"


def _format_range(metric: MetricSummary) -> str:
    if not metric.auto_scored or metric.minimum is None or metric.maximum is None:
        return "—"
    if metric.stdev is not None and metric.stdev > 0:
        return f"{metric.minimum:.4f}–{metric.maximum:.4f} (σ {metric.stdev:.4f})"
    return "stable"


def to_markdown(report: BenchmarkReport) -> str:
    lines: list[str] = []
    lines.append(f"# Provider benchmark — `{report.provider_set}` set")
    lines.append("")
    lines.append(
        f"Runs: {report.runs} · Total cost: {report.total_cost_minor} minor "
        f"({report.currency}) · Cost cap: "
        f"{report.cost_cap_minor if report.cost_cap_minor is not None else 'none'}"
    )
    if report.halted:
        lines.append("")
        lines.append(f"> **Run halted by the cost cap.** {report.halt_reason}")
    lines.append("")
    lines.append(
        "> **Interpretation:** the best score is not the winner if the provider cannot lawfully "
        "receive the input. Read the *Data region* and *Face/voice OK* columns before the metric "
        "columns."
    )
    lines.append("")
    lines.append(
        "| Capability | Provider | Model | Data region | Face/voice OK | Runs | Samples | "
        "Invalid output | Cost (minor) | Prompt ver | Route rev |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for capability in report.capabilities:
        lines.append(
            f"| {capability.capability.value} | {capability.provider} | {capability.model} | "
            f"{capability.data_region} | {'yes' if capability.face_voice_input_allowed else 'NO'} | "
            f"{capability.runs} | {capability.sample_count} | "
            f"{capability.invalid_output_rate:.2%} | {capability.total_cost_minor} | "
            f"{', '.join(capability.prompt_versions)} | {', '.join(capability.route_revisions)} |"
        )
    lines.append("")
    for capability in report.capabilities:
        lines.append(f"## {capability.capability.value}")
        lines.append("")
        lines.append("| Metric | Unit | Value | Range across runs | Auto-scored | Note |")
        lines.append("|---|---|---|---|---|---|")
        for metric in capability.metrics:
            lines.append(
                f"| {metric.name} | {metric.unit} | {_format_value(metric)} | "
                f"{_format_range(metric)} | {'yes' if metric.auto_scored else 'no'} | "
                f"{metric.note} |"
            )
        lines.append("")
    return "\n".join(lines)
