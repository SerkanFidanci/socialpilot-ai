"""Celery task registration for the durable outbox dispatcher boundary."""

from __future__ import annotations

import structlog

from app.infrastructure.celery_app import celery_app

logger = structlog.get_logger(__name__)


@celery_app.task(name="socialpilot.operations.dispatch_outbox")  # type: ignore[misc]
def dispatch_outbox() -> dict[str, str]:
    """Transport trigger only; worker composition injects the publisher port later."""

    logger.info("outbox_dispatch_triggered")
    return {"status": "triggered"}


@celery_app.task(name="socialpilot.media.ingest")  # type: ignore[misc]
def media_ingest() -> dict[str, str]:
    """Durable delivery trigger; worker composition injects ingest dependencies."""

    logger.info("media_ingest_triggered")
    return {"status": "triggered"}
