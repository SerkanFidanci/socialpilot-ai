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

from app.infrastructure.celery_app import celery_app
from app.worker.composition import (
    WorkerContext,
    get_worker_context,
    shutdown_worker_process,
    start_worker_process,
)


async def _drain(
    context: WorkerContext,
    factory: Callable[[Any], Any],
    *,
    needs_workdir: bool,
) -> dict[str, object]:
    processed = 0
    root = Path(context.settings.worker_temp_root)
    for _ in range(context.settings.worker_drain_batch_size):
        async with context.database.session_factory() as session:
            service = factory(session)
            if needs_workdir:
                result = await service.process_next(workdir=root)
            else:
                result = await service.process_next()
        if result is None:
            break
        processed += 1
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
            event = await context.outbox_dispatcher(session).dispatch_one()
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
