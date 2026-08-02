"""Worker process ownership, event-loop safety, and safe drain behavior."""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from sqlalchemy.exc import NoReferencedTableError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import logging as app_logging
from app.core.config import Settings
from app.infrastructure.database.session import Database
from app.worker import composition
from app.worker.composition import (
    WorkerContext,
    build_worker_context,
    get_worker_context,
    shutdown_worker_process,
    start_worker_process,
)
from app.worker.tasks import _drain


def settings(**changes: object) -> Settings:
    values: dict[str, object] = {
        "app_env": "test",
        "database_url": "postgresql+asyncpg://test:test@localhost:5432/test",
        "redis_url": "redis://localhost:6379/0",
        "celery_broker_url": "redis://localhost:6379/1",
        "celery_result_backend": "redis://localhost:6379/2",
    }
    values.update(changes)
    return Settings(**values)  # type: ignore[arg-type]


@pytest.fixture
def released_context() -> Iterator[list[WorkerContext]]:
    """Close every loop a test builds so pytest never inherits a stray worker loop."""

    built: list[WorkerContext] = []
    try:
        yield built
    finally:
        for context in built:
            if not context.loop.is_closed():
                context.loop.run_until_complete(context.database.dispose())
                context.loop.close()
        composition._context = None
        asyncio.set_event_loop(None)


def test_context_builds_one_database_and_rejects_production_fakes(
    tmp_path: Path, released_context: list[WorkerContext]
) -> None:
    context = build_worker_context(settings(worker_temp_root=str(tmp_path)))
    released_context.append(context)
    assert context.database is context.database
    with pytest.raises(RuntimeError, match="WORKER_PRODUCTION_ADAPTERS_NOT_CONFIGURED"):
        build_worker_context(
            settings(worker_temp_root=str(tmp_path)).model_copy(update={"app_env": "production"})
        )


def test_consecutive_runs_reuse_one_process_loop(
    tmp_path: Path, released_context: list[WorkerContext]
) -> None:
    """Pooled asyncpg connections are loop-bound, so every task must use the same loop."""

    context = build_worker_context(settings(worker_temp_root=str(tmp_path)))
    released_context.append(context)

    async def observed_loop() -> asyncio.AbstractEventLoop:
        return asyncio.get_running_loop()

    first = context.run(observed_loop())
    second = context.run(observed_loop())
    assert first is second is context.loop


def test_run_refuses_reentrant_and_closed_loops(
    tmp_path: Path, released_context: list[WorkerContext]
) -> None:
    context = build_worker_context(settings(worker_temp_root=str(tmp_path)))
    released_context.append(context)

    async def noop() -> None:
        return None

    def rejects(match: str) -> None:
        coroutine = noop()
        try:
            with pytest.raises(RuntimeError, match=match):
                context.run(coroutine)
        finally:
            coroutine.close()

    async def reentrant() -> None:
        rejects("WORKER_EVENT_LOOP_REENTRANT")

    context.run(reentrant())
    context.loop.run_until_complete(context.database.dispose())
    context.loop.close()
    rejects("WORKER_EVENT_LOOP_CLOSED")


def test_shutdown_disposes_engine_closes_loop_and_is_idempotent(tmp_path: Path) -> None:
    disposed = 0

    async def dispose() -> None:
        nonlocal disposed
        disposed += 1

    context = replace(
        build_worker_context(settings(worker_temp_root=str(tmp_path))),
        database=cast(Database, SimpleNamespace(dispose=dispose)),
    )
    composition._context = context
    try:
        shutdown_worker_process()
        assert disposed == 1
        assert context.loop.is_closed()
        assert composition._context is None
        shutdown_worker_process()
        assert disposed == 1
    finally:
        composition._context = None
        asyncio.set_event_loop(None)


def test_restart_releases_the_previous_loop_without_leaking_a_context(tmp_path: Path) -> None:
    start_worker_process()
    first = get_worker_context()
    try:
        start_worker_process()
        second = get_worker_context()
        assert first.loop.is_closed()
        assert second.loop is not first.loop
        assert not second.loop.is_closed()
    finally:
        shutdown_worker_process()
        assert composition._context is None
        asyncio.set_event_loop(None)


def test_worker_process_init_installs_the_signature_scrubber(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The worker is the one process that never calls `configure_logging`.

    Celery owns the handlers here, so the scrubber has to be installed by the process init or
    a materializer's HTTP client could log a presigned URL that no other process would.
    """

    signature = "a" * 64
    monkeypatch.setattr(app_logging, "_redaction_installed", False)
    previous_factory = logging.getLogRecordFactory()
    logging.setLogRecordFactory(logging.LogRecord)
    try:
        start_worker_process()
        record = logging.getLogRecordFactory()(
            "vendor.sdk",
            logging.INFO,
            __file__,
            1,
            "PUT %s",
            (f"?X-Amz-Signature={signature}",),
            None,
        )
        assert signature not in record.getMessage()
    finally:
        logging.setLogRecordFactory(previous_factory)
        shutdown_worker_process()
        asyncio.set_event_loop(None)


@pytest.mark.asyncio
async def test_drain_stops_when_empty_and_obeys_batch_limit(tmp_path: Path) -> None:
    sessions = 0
    closed = 0
    calls = 0

    class SessionFactory:
        def __call__(self) -> SessionFactory:
            return self

        async def __aenter__(self) -> object:
            nonlocal sessions
            sessions += 1
            return object()

        async def __aexit__(self, *_: object) -> None:
            nonlocal closed
            closed += 1

    class Service:
        async def process_next(self, *, workdir: Path) -> object | None:
            nonlocal calls
            assert workdir == tmp_path
            calls += 1
            return object() if calls <= 2 else None

    context = SimpleNamespace(
        settings=SimpleNamespace(worker_drain_batch_size=5, worker_temp_root=str(tmp_path)),
        database=SimpleNamespace(session_factory=SessionFactory()),
    )
    result = await _drain(cast(WorkerContext, context), lambda _: Service(), needs_workdir=True)
    assert result == {"status": "drained", "processed": 2}
    assert sessions == 3
    assert closed == sessions, "every drain iteration must close its session"


def test_worker_entry_point_alone_resolves_every_cross_module_foreign_key() -> None:
    """Run in a fresh process: importing this suite would hide a missing model import.

    The Celery entry point imports fewer model modules than the API. Without complete
    metadata, `jobs.business_id` cannot resolve `businesses.id` and every task that
    loads a row fails with `NoReferencedTableError`.
    """

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import app.infrastructure.celery_app, app.worker.tasks;"
            "from app.infrastructure.database.metadata import verify_mapping_is_complete;"
            "print(verify_mapping_is_complete())",
        ],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[2],
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    assert int(completed.stdout.strip().splitlines()[-1]) > 0


def test_worker_context_build_fails_fast_on_incomplete_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, released_context: list[WorkerContext]
) -> None:
    def broken() -> int:
        raise NoReferencedTableError("businesses is not registered", "businesses")

    monkeypatch.setattr(composition, "verify_mapping_is_complete", broken)
    with pytest.raises(NoReferencedTableError):
        build_worker_context(settings(worker_temp_root=str(tmp_path)))
    assert released_context == []


def test_celery_drain_tasks_are_registered() -> None:
    import app.worker.tasks  # noqa: F401
    from app.infrastructure.celery_app import celery_app

    assert {
        "media.ingest.drain",
        "media.technical_analysis.drain",
        "media.scene_speech_analysis.drain",
        "media.video_understanding.drain",
        "content.render.drain",
        "content.qc.drain",
        "operations.outbox.dispatch",
        "operations.recovery.drain",
    }.issubset(celery_app.tasks)


def test_every_scheduled_task_is_registered_and_every_registered_drain_is_scheduled() -> None:
    """Totality both ways, so a task and its beat entry cannot be added one without the other.

    Written as a set relation rather than a list because this repository keeps relearning the
    same lesson: a hand-counted set is the thing the next round finds a hole in. A drain that
    exists but is never woken is dead code that looks alive, and a beat entry naming a task
    nobody registered is a worker that logs an unregistered-task error every tick.
    """

    import app.worker.tasks  # noqa: F401
    from app.infrastructure.celery_app import celery_app

    scheduled = {entry["task"] for entry in celery_app.conf.beat_schedule.values()}
    registered = {name for name in celery_app.tasks if not name.startswith("celery.")}
    assert scheduled <= registered, scheduled - registered
    assert registered <= scheduled, registered - scheduled


def test_the_qc_drain_runs_inside_the_guarded_scratch_root(tmp_path: Path) -> None:
    """QC materializes an output and writes metadata dumps and frames; all of it is scratch.

    `needs_workdir=True` is what puts the run inside the budget `WorkerScratchGuard` enforces
    and what gets it swept afterwards — the same discipline the render drain follows.
    """

    import inspect

    from app.worker import tasks

    source = inspect.getsource(tasks.drain_content_qc)
    assert "content_qc_service" in source
    assert "needs_workdir=True" in source


def test_the_qc_service_the_worker_builds_cannot_render(
    tmp_path: Path, released_context: list[WorkerContext]
) -> None:
    """The composition root is where a render port would have to leak in. It does not.

    Slice 2E owns automatic re-render and the attempt limit that bounds it. Handing the QC
    service a render port here would create the loop before the bound exists, and no test inside
    the content module would notice — the wiring is the only place it could happen.
    """

    context = build_worker_context(settings(worker_temp_root=str(tmp_path)))
    released_context.append(context)
    service = context.content_qc_service(cast(AsyncSession, SimpleNamespace()))
    collaborators = {
        name: value for name, value in vars(service).items() if not name.startswith("__")
    }
    assert not any(isinstance(value, type(context.render)) for value in collaborators.values())
    assert context.qc_probe is not None and context.visual_qc is not None


def test_the_worker_measures_with_the_real_probe_and_a_disabled_eye_in_production(
    tmp_path: Path, released_context: list[WorkerContext]
) -> None:
    """Two adapters, two opposite rules, and the worker is where both have to hold.

    Measurement has no fixture in any environment, because the probe *is* the guarantee that
    nobody's account of the output is taken at face value. Vision is the reverse: its fixture
    produces an approval a reviewer would act on, so production gets the adapter that declines
    and the four model checks land `unknown`.
    """

    from app.infrastructure.ai.fake_visual_qc import DisabledVisualQcAdapter, FakeVisualQcAdapter
    from app.infrastructure.render.qc_probe import FFmpegQcProbe

    context = build_worker_context(settings(worker_temp_root=str(tmp_path)))
    released_context.append(context)
    assert isinstance(context.qc_probe, FFmpegQcProbe)
    assert isinstance(context.visual_qc, FakeVisualQcAdapter)

    from app.infrastructure.ai import create_visual_qc
    from app.infrastructure.render import create_qc_probe

    production = settings(worker_temp_root=str(tmp_path))
    production.app_env = "production"
    assert isinstance(create_qc_probe(production), FFmpegQcProbe)
    assert isinstance(create_visual_qc(production), DisabledVisualQcAdapter)
