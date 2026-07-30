"""Provider benchmark harness (PRD §40.5, W08).

An offline measurement tool, not a domain module: it selects no provider and persists nothing.
It runs the golden set through a provider set (fake by default), scores output against
machine-readable ground truth, and attributes cost/latency through a single neutral
``ProviderUsageRecord`` shape (ADR-007) so no parallel cost model is created.
"""

from __future__ import annotations

from app.benchmark.golden import GoldenSample, load_samples
from app.benchmark.model import (
    BenchmarkCostCapExceeded,
    BenchmarkDataMinimizationError,
    BenchmarkError,
    BenchmarkProvenanceError,
    BenchmarkReport,
    Capability,
    ProviderUsageRecord,
)
from app.benchmark.providers import ProviderRegistry, build_registry
from app.benchmark.report import to_json_dict, to_markdown
from app.benchmark.runner import run_benchmark

__all__ = [
    "BenchmarkCostCapExceeded",
    "BenchmarkDataMinimizationError",
    "BenchmarkError",
    "BenchmarkProvenanceError",
    "BenchmarkReport",
    "Capability",
    "GoldenSample",
    "ProviderRegistry",
    "ProviderUsageRecord",
    "build_registry",
    "load_samples",
    "run_benchmark",
    "to_json_dict",
    "to_markdown",
]
