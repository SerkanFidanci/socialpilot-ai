"""Celery wake-up tasks that drain durable PostgreSQL jobs without trusting payload IDs."""
# mypy: disable-error-code=misc

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from celery.signals import (  # type: ignore[import-untyped]
    worker_process_init,
    worker_process_shutdown,
)

from app.core.telemetry import continue_trace
from app.infrastructure.celery_app import celery_app
from app.worker.composition import (
    WorkerContext,
    get_worker_context,
    shutdown_worker_process,
    start_worker_process,
)
from app.worker.scratch import WorkerScratchGuard


async def _drain(
    context: WorkerContext,
    factory: Callable[[Any], Any],
    *,
    needs_workdir: bool,
) -> dict[str, object]:
    processed = 0
    root = Path(context.settings.worker_temp_root)
    guard = WorkerScratchGuard(root)
    for _ in range(context.settings.worker_drain_batch_size):
        # Refuse to start another job while scratch is over budget. On a single server this
        # fails loudly with WORKER_SCRATCH_BUDGET_EXCEEDED instead of piling more work onto a
        # near-full disk; the job the drain would have claimed stays unclaimed and is
        # recovered later. Checked before every item so residue a subprocess left outside its
        # TemporaryDirectory cannot accumulate silently across the batch.
        guard.ensure_within_budget()
        async with context.database.session_factory() as session:
            service = factory(session)
            if needs_workdir:
                result = await service.process_next(workdir=root)
            else:
                result = await service.process_next()
        if result is None:
            break
        processed += 1
        # Belt-and-suspenders cleanup: services already remove their own TemporaryDirectory,
        # but sweep anything a subprocess left behind so the next item starts from a clean
        # scratch. Only runs for workdir-backed drains that actually touch disk.
        if needs_workdir:
            guard.reclaim_stale()
    return {"status": "drained", "processed": processed}


async def _recover(context: WorkerContext) -> dict[str, object]:
    async with context.database.session_factory() as session:
        jobs = await context.recovery_service(session).recover_stale_running_jobs(
            business_id=None, limit=context.settings.worker_drain_batch_size
        )
    return {"status": "recovered", "processed": len(jobs)}


async def _dispatch_outbox(context: WorkerContext) -> dict[str, object]:
    processed = 0
    for _ in range(context.settings.worker_drain_batch_size):
        async with context.database.session_factory() as session:
            # The envelope carries the `traceparent` of the request that wrote the event, so
            # publishing inside that context puts the drain task this wakes into the same trace
            # instead of starting a second island at the beat tick. Inert when telemetry is off.
            event = await context.outbox_dispatcher(session).dispatch_one(
                publish_scope=continue_trace
            )
        if event is None:
            break
        processed += 1
    return {"status": "dispatched", "processed": processed}


@celery_app.task(name="media.ingest.drain")
def drain_media_ingest() -> dict[str, object]:
    context = get_worker_context()
    return context.run(_drain(context, context.ingest_service, needs_workdir=False))


@celery_app.task(name="media.technical_analysis.drain")
def drain_technical_analysis() -> dict[str, object]:
    context = get_worker_context()
    return context.run(_drain(context, context.technical_service, needs_workdir=True))


@celery_app.task(name="media.scene_speech_analysis.drain")
def drain_scene_speech_analysis() -> dict[str, object]:
    context = get_worker_context()
    return context.run(_drain(context, context.scene_speech_service, needs_workdir=True))


@celery_app.task(name="media.video_understanding.drain")
def drain_video_understanding() -> dict[str, object]:
    context = get_worker_context()
    return context.run(_drain(context, context.video_understanding_service, needs_workdir=True))


@celery_app.task(name="content.render.drain")
def drain_content_render() -> dict[str, object]:
    """Drain render jobs. `needs_workdir` keeps every render inside the guarded scratch root."""

    context = get_worker_context()
    return context.run(_drain(context, context.content_render_service, needs_workdir=True))


@celery_app.task(name="content.qc.drain")
def drain_content_qc() -> dict[str, object]:
    """Drain automatic QC (PRD §19.4).

    Slice 2D had no producer for this drain and the beat tick was its whole trigger; slice 2E
    gives it `content.qc.requested`, written by the render path in the transaction that makes a
    render succeed. The claim still finds a succeeded render that no report has opened over, so
    a render that finished while this worker was down is picked up by the sweep — the event
    makes that the exception rather than the mechanism. `needs_workdir` keeps the materialized
    output, the metadata dumps and the sampled frames inside the guarded scratch root.
    """

    context = get_worker_context()
    return context.run(_drain(context, context.content_qc_service, needs_workdir=True))


@celery_app.task(name="content.project.drain")
def drain_content_projects() -> dict[str, object]:
    """Advance content projects one step each (PRD §20).

    No workdir: the sequencer touches no media. Its steps call services that do, and each of
    those keeps its own scratch inside the guarded root.
    """

    context = get_worker_context()
    return context.run(_drain(context, context.content_project_service, needs_workdir=False))


@celery_app.task(name="content.pending.sweep")
def sweep_abandoned_runs() -> dict[str, object]:
    """Settle script and voiceover runs that opened, were possibly billed, and never returned.

    The one drain here with no producer at all — by design. There is no event for "a process
    died", which is exactly the condition this exists to notice; the tick is the only thing that
    can observe an absence.
    """

    context = get_worker_context()
    return context.run(_drain(context, context.abandoned_run_sweeper, needs_workdir=False))


@celery_app.task(name="entitlement.reservation.sweep")
def sweep_abandoned_reservations() -> dict[str, object]:
    """Release credit holds whose work can no longer settle them (PRD §12.7).

    In a healthy system this finds nothing, because a reservation is settled by the transaction
    that makes its project terminal. It is here for the case that atomicity cannot cover — a
    source row that went away — and because a hold nobody will ever close is a customer's credit
    held forever, which is the failure this whole slice exists to make impossible.
    """

    context = get_worker_context()
    return context.run(_drain(context, context.entitlement_sweeper, needs_workdir=False))


@celery_app.task(name="operations.recovery.drain")
def recover_stale_jobs() -> dict[str, object]:
    context = get_worker_context()
    return context.run(_recover(context))


@celery_app.task(name="operations.outbox.dispatch")
def dispatch_outbox() -> dict[str, object]:
    context = get_worker_context()
    return context.run(_dispatch_outbox(context))


@worker_process_init.connect
def _on_worker_process_init(**_: object) -> None:
    start_worker_process()


@worker_process_shutdown.connect
def _on_worker_process_shutdown(**_: object) -> None:
    shutdown_worker_process()
