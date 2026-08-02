"""Celery application configuration without domain task registration."""

from __future__ import annotations

from celery import Celery  # type: ignore[import-untyped]

from app.core.config import Settings, get_settings


def create_celery_app(settings: Settings) -> Celery:
    """Create the broker-backed Celery application for future worker tasks."""

    application = Celery(
        "socialpilot",
        broker=settings.celery_broker_url,
        backend=settings.celery_result_backend,
    )
    application.conf.update(
        include=["app.worker.tasks"],
        task_default_queue="default",
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        task_time_limit=settings.celery_task_timeout_seconds,
        task_soft_time_limit=settings.celery_task_soft_time_limit_seconds,
        worker_prefetch_multiplier=1,
        task_acks_late=True,
        # Pin the Celery 6 default explicitly so worker startup logs no deprecation warning.
        broker_connection_retry_on_startup=True,
        beat_schedule={
            "dispatch-outbox": {
                "task": "operations.outbox.dispatch",
                "schedule": settings.celery_beat_outbox_interval_seconds,
            },
            "drain-ingest": {
                "task": "media.ingest.drain",
                "schedule": settings.celery_beat_media_drain_interval_seconds,
            },
            "drain-technical": {
                "task": "media.technical_analysis.drain",
                "schedule": settings.celery_beat_media_drain_interval_seconds,
            },
            "drain-scene-speech": {
                "task": "media.scene_speech_analysis.drain",
                "schedule": settings.celery_beat_media_drain_interval_seconds,
            },
            "drain-video-understanding": {
                "task": "media.video_understanding.drain",
                "schedule": settings.celery_beat_media_drain_interval_seconds,
            },
            "drain-content-render": {
                "task": "content.render.drain",
                "schedule": settings.celery_beat_media_drain_interval_seconds,
            },
            # Automatic QC (§19.4) is woken by `content.qc.requested` the moment a render
            # succeeds, so this tick is the net rather than the trigger: it catches a render
            # that finished while the worker was down, or an event the broker lost. Slice 2D
            # measured the always-on scan this replaces at 134 ms per tick over 200k renders.
            "sweep-content-qc": {
                "task": "content.qc.drain",
                "schedule": settings.celery_beat_qc_sweep_interval_seconds,
            },
            "drain-content-projects": {
                "task": "content.project.drain",
                "schedule": settings.celery_beat_media_drain_interval_seconds,
            },
            # The two entries here with no event behind them, and neither can have one: nothing
            # emits "a process died mid-call" or "a customer stopped caring", which are exactly
            # the conditions these sweeps exist to notice. A tick is the only thing that can
            # observe an absence.
            "sweep-abandoned-runs": {
                "task": "content.pending.sweep",
                "schedule": settings.celery_beat_pending_sweep_interval_seconds,
            },
            "sweep-abandoned-projects": {
                "task": "content.project.sweep",
                "schedule": settings.celery_beat_project_sweep_interval_seconds,
            },
            # Reconciliation over a settlement that is already atomic: the transaction that ends
            # a project also closes its credit hold, so this tick exists for the one case that
            # cannot cover — a source row that is gone. Hourly, because in a healthy system it
            # finds nothing.
            "sweep-entitlement-reservations": {
                "task": "entitlement.reservation.sweep",
                "schedule": settings.celery_beat_entitlement_sweep_interval_seconds,
            },
            # PRD §13's three planner drains. The first joins the two sweeps above as an entry
            # with no event behind it, and for the same kind of reason: nothing emits "a new
            # period began", and a tick is the only thing that can observe the arrival of a date.
            # The other two *could* be woken by an event and are not in this slice — the outbox
            # envelope is not this work order's file — so their intervals are the whole latency
            # budget, which is why they are short.
            "plan-content-obligations": {
                "task": "planner.obligations.plan",
                "schedule": settings.celery_beat_planner_plan_interval_seconds,
            },
            "dispatch-content-obligations": {
                "task": "planner.obligations.dispatch",
                "schedule": settings.celery_beat_planner_dispatch_interval_seconds,
            },
            "schedule-approved-projects": {
                "task": "planner.projects.schedule",
                "schedule": settings.celery_beat_planner_schedule_interval_seconds,
            },
            "recover-stale-jobs": {
                "task": "operations.recovery.drain",
                "schedule": settings.celery_beat_recovery_interval_seconds,
            },
        },
    )
    return application


celery_app = create_celery_app(get_settings())
