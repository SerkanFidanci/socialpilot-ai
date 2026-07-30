"""Run the provider benchmark over the golden set.

Default behaviour is credential-free and DB-free: fake providers, no network. It exists so the
harness is exercised in CI and so a real provider can later be measured the same way.

Run from ``services/api``:

    python -m scripts.run_benchmark
    python -m scripts.run_benchmark --runs 5 --cost-cap-minor 40 --out results.json

A real provider set is a configuration surface only; no credential is bundled. Selecting one
raises with the expected configuration rather than silently doing nothing.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.benchmark import build_registry, load_samples, run_benchmark, to_json_dict, to_markdown
from app.benchmark.model import BenchmarkError
from app.core.config import Settings


def _settings() -> Settings:
    """A non-connecting settings object; the benchmark never opens a socket in the fake set."""

    return Settings(
        app_env="test",
        storage_adapter="fake",
        materializer_adapter="fake",
        database_url="postgresql+asyncpg://benchmark:benchmark@localhost:5432/benchmark",
        redis_url="redis://localhost:6379/0",
        celery_broker_url="redis://localhost:6379/1",
        celery_result_backend="redis://localhost:6379/2",
    )


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Provider benchmark harness (W08).")
    parser.add_argument("--provider-set", default="fake", help="Provider set (default: fake).")
    parser.add_argument("--runs", type=int, default=1, help="Number of runs for a distribution.")
    parser.add_argument(
        "--cost-cap-minor",
        type=int,
        default=None,
        help="Halt when the next call's estimated cost would exceed this integer-minor cap.",
    )
    parser.add_argument("--out", type=Path, default=None, help="Write machine-readable JSON here.")
    parser.add_argument(
        "--markdown-out", type=Path, default=None, help="Write the comparison table here."
    )
    parser.add_argument(
        "--samples-dir", type=Path, default=None, help="Override the golden samples directory."
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        registry = build_registry(args.provider_set)
        samples = load_samples(args.samples_dir)
        report = run_benchmark(
            registry=registry,
            samples=samples,
            settings=_settings(),
            runs=args.runs,
            cost_cap_minor=args.cost_cap_minor,
        )
    except BenchmarkError as error:
        print(f"benchmark error: {error}", file=sys.stderr)
        return 1

    markdown = to_markdown(report)
    if args.out is not None:
        args.out.write_text(json.dumps(to_json_dict(report), indent=2, ensure_ascii=False), "utf-8")
        print(f"wrote machine-readable results to {args.out}", file=sys.stderr)
    if args.markdown_out is not None:
        args.markdown_out.write_text(markdown, encoding="utf-8")
        print(f"wrote comparison table to {args.markdown_out}", file=sys.stderr)
    print(markdown)
    # A cost-cap halt is a signal, not a crash: partial results are still emitted, exit non-zero.
    return 2 if report.halted else 0


if __name__ == "__main__":
    raise SystemExit(main())
