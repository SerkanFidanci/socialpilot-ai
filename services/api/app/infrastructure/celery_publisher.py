"""Outbox publisher that wakes durable PostgreSQL drain tasks through Celery."""

from __future__ import annotations

import asyncio

from celery import Celery  # type: ignore[import-untyped]
from kombu.exceptions import OperationalError  # type: ignore[import-untyped]

from app.modules.operations.models import OutboxEvent
from app.modules.operations.service import PublishError, TransientPublishError

DRAIN_TASK_BY_EVENT: dict[str, str] = {
    "media.ingest.requested": "media.ingest.drain",
    "media.technical_analysis.requested": "media.technical_analysis.drain",
    "media.scene_speech.requested": "media.scene_speech_analysis.drain",
    "media.video_understanding.requested": "media.video_understanding.drain",
    "content.render.requested": "content.render.drain",
    # Slice 2E gives automatic QC the producer it did not have. Until now `content.qc.drain` was
    # the one drain woken only by the beat tick, and its claim scanned for succeeded renders with
    # no report; the render path now announces itself, and the tick drops to a rare sweep.
    "content.qc.requested": "content.qc.drain",
    "content.project.advance.requested": "content.project.drain",
}
"""Requested events whose only transport effect is waking the matching drain task."""

NOTIFICATION_ONLY_EVENTS: frozenset[str] = frozenset(
    {
        "media.technical_analysis.completed",
        "media.scene_speech.completed",
        "media.video_understanding.completed",
    }
)
"""Completion events with no Phase 1D subscriber.

Each pipeline step creates its successor job inside its own completion transaction, so
these events drive no work and must not enqueue a task. They are recorded as delivered
to an empty subscriber set rather than dead-lettered, which would otherwise flag every
successful analysis as an operational failure.
"""


class CeleryOutboxPublisher:
    """Publish only a wake-up signal; messages deliberately carry no domain identity."""

    def __init__(self, celery: Celery) -> None:
        self._celery = celery

    async def publish(self, event: OutboxEvent) -> None:
        """Enqueue the drain task for a routable event; never forward the durable payload.

        The message carries no arguments at all, so no object key, signed URL, tenant
        identity, credential, or media byte can reach the broker. A worker re-reads
        everything it needs from PostgreSQL under a tenant-scoped claim.
        """

        task_name = DRAIN_TASK_BY_EVENT.get(event.event_type)
        if task_name is None:
            if event.event_type in NOTIFICATION_ONLY_EVENTS:
                return
            raise PublishError("OUTBOX_EVENT_TYPE_UNSUPPORTED")
        try:
            await asyncio.to_thread(self._celery.send_task, task_name, args=(), kwargs={})
        except (OperationalError, OSError, TimeoutError) as error:
            raise TransientPublishError("CELERY_ENQUEUE_UNAVAILABLE") from error
        except Exception as error:  # Unclassified handoff failures are not retried.
            raise PublishError("CELERY_ENQUEUE_REJECTED") from error
