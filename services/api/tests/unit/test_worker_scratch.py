"""Worker scratch budget enforcement and orphan reclamation."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from app.worker import tasks
from app.worker.composition import WorkerContext
from app.worker.scratch import (
    WORKER_SCRATCH_MAX_BYTES,
    WorkerScratchExhausted,
    WorkerScratchGuard,
)
from app.worker.tasks import _drain


def _write(path: Path, size: int) -> None:
    path.write_bytes(b"x" * size)


def test_budget_is_below_the_hard_tmpfs_wall() -> None:
    """The soft guard must trip before the OS returns ENOSPC, with headroom to unwind."""

    from app.core.config import WORKER_TMPFS_BYTES

    assert 0 < WORKER_SCRATCH_MAX_BYTES < WORKER_TMPFS_BYTES


def test_usage_bytes_sums_files_recursively(tmp_path: Path) -> None:
    _write(tmp_path / "a.bin", 100)
    nested = tmp_path / "job" / "deep"
    nested.mkdir(parents=True)
    _write(nested / "b.bin", 250)

    assert WorkerScratchGuard(tmp_path).usage_bytes() == 350


def test_reclaim_stale_removes_orphans_and_reports_bytes(tmp_path: Path) -> None:
    _write(tmp_path / "loose.bin", 40)
    job = tmp_path / "scene-speech-abc"
    (job / "frames").mkdir(parents=True)
    _write(job / "frames" / "f.bin", 60)

    reclaimed = WorkerScratchGuard(tmp_path).reclaim_stale()

    assert reclaimed == 100
    assert list(tmp_path.iterdir()) == []


def test_reclaim_stale_is_safe_on_a_missing_root(tmp_path: Path) -> None:
    assert WorkerScratchGuard(tmp_path / "absent").reclaim_stale() == 0


def test_ensure_within_budget_raises_documented_code_when_over(tmp_path: Path) -> None:
    _write(tmp_path / "big.bin", 200)
    guard = WorkerScratchGuard(tmp_path, max_bytes=100)

    with pytest.raises(WorkerScratchExhausted) as excinfo:
        guard.ensure_within_budget()

    assert excinfo.value.error_code == "WORKER_SCRATCH_BUDGET_EXCEEDED"
    assert excinfo.value.usage_bytes == 200
    assert excinfo.value.max_bytes == 100


def test_ensure_within_budget_passes_when_under(tmp_path: Path) -> None:
    _write(tmp_path / "small.bin", 50)
    WorkerScratchGuard(tmp_path, max_bytes=100).ensure_within_budget()


def _drain_context(tmp_path: Path) -> WorkerContext:
    class SessionFactory:
        def __call__(self) -> SessionFactory:
            return self

        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *_: object) -> None:
            return None

    return cast(
        WorkerContext,
        SimpleNamespace(
            settings=SimpleNamespace(worker_drain_batch_size=5, worker_temp_root=str(tmp_path)),
            database=SimpleNamespace(session_factory=SessionFactory()),
        ),
    )


@pytest.mark.asyncio
async def test_drain_refuses_when_scratch_over_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An over-budget scratch fails the drain loudly and never claims a job."""

    _write(tmp_path / "leftover.bin", 500)
    monkeypatch.setattr(tasks, "WorkerScratchGuard", lambda root: WorkerScratchGuard(root, 10))

    calls = 0

    class Service:
        async def process_next(self, *, workdir: Path) -> object | None:
            nonlocal calls
            calls += 1
            return object()

    with pytest.raises(WorkerScratchExhausted):
        await _drain(_drain_context(tmp_path), lambda _: Service(), needs_workdir=True)

    assert calls == 0, "no job may be claimed while scratch is over budget"


@pytest.mark.asyncio
async def test_drain_reclaims_residue_between_jobs(tmp_path: Path) -> None:
    """Residue a subprocess leaves outside its TemporaryDirectory is swept each iteration."""

    calls = 0

    class Service:
        async def process_next(self, *, workdir: Path) -> object | None:
            nonlocal calls
            calls += 1
            if calls > 2:
                return None
            _write(workdir / f"residue-{calls}.bin", 128)
            return object()

    result = await _drain(_drain_context(tmp_path), lambda _: Service(), needs_workdir=True)

    assert result == {"status": "drained", "processed": 2}
    assert list(tmp_path.iterdir()) == [], "each drained job's scratch residue must be reclaimed"
