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
            "recover-stale-jobs": {
                "task": "operations.recovery.drain",
                "schedule": settings.celery_beat_recovery_interval_seconds,
            },
        },
    )
    return application


celery_app = create_celery_app(get_settings())
