"""Celery outbox wake-up mapping, payload safety, and beat registration contracts."""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from kombu.exceptions import OperationalError  # type: ignore[import-untyped]

from app.core.config import Settings
from app.infrastructure.celery_app import create_celery_app
from app.infrastructure.celery_publisher import (
    DRAIN_TASK_BY_EVENT,
    NOTIFICATION_ONLY_EVENTS,
    CeleryOutboxPublisher,
)
from app.modules.operations.models import OutboxEvent
from app.modules.operations.service import PublishError, TransientPublishError


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


def event(event_type: str, **payload: object) -> OutboxEvent:
    return cast(OutboxEvent, SimpleNamespace(event_type=event_type, payload=payload))


@pytest.mark.asyncio
async def test_publisher_maps_requested_events_to_empty_wake_up_messages() -> None:
    application = create_celery_app(settings())
    sent: list[tuple[str, tuple[object, ...], dict[str, object]]] = []
    application.send_task = lambda name, args, kwargs: sent.append((name, args, kwargs))
    publisher = CeleryOutboxPublisher(application)
    for event_type, task_name in (
        ("media.ingest.requested", "media.ingest.drain"),
        ("media.technical_analysis.requested", "media.technical_analysis.drain"),
        ("media.scene_speech.requested", "media.scene_speech_analysis.drain"),
        ("media.video_understanding.requested", "media.video_understanding.drain"),
        ("content.render.requested", "content.render.drain"),
    ):
        await publisher.publish(event(event_type))
        assert sent[-1] == (task_name, (), {})
    assert len(sent) == len(DRAIN_TASK_BY_EVENT)


@pytest.mark.asyncio
async def test_wake_up_message_never_carries_keys_urls_or_credentials() -> None:
    """A durable payload stays in PostgreSQL; the broker only receives a task name."""

    application = create_celery_app(settings())
    sent: list[tuple[str, tuple[object, ...], dict[str, object]]] = []
    application.send_task = lambda name, args, kwargs: sent.append((name, args, kwargs))
    await CeleryOutboxPublisher(application).publish(
        event(
            "media.video_understanding.requested",
            job_id="8ac1f0be-0000-4000-8000-000000000001",
            asset_id="8ac1f0be-0000-4000-8000-000000000002",
            storage_object_key="tenant/secret/media/original",
            signed_url="https://storage.example.test/o?X-Amz-Signature=deadbeef",
            credential="provider-api-key",
        )
    )
    assert sent == [("media.video_understanding.drain", (), {})]


@pytest.mark.asyncio
async def test_notification_only_completion_events_enqueue_nothing() -> None:
    application = create_celery_app(settings())
    sent: list[str] = []
    application.send_task = lambda name, args, kwargs: sent.append(name)
    publisher = CeleryOutboxPublisher(application)
    for event_type in sorted(NOTIFICATION_ONLY_EVENTS):
        await publisher.publish(event(event_type))
    assert sent == []


@pytest.mark.asyncio
async def test_unknown_event_is_rejected_without_touching_the_broker() -> None:
    application = create_celery_app(settings())
    application.send_task = lambda *_args, **_kwargs: pytest.fail("unknown event reached broker")
    publisher = CeleryOutboxPublisher(application)
    for event_type in ("unknown", "media.ingest", "media.ingest.requested.extra", ""):
        with pytest.raises(PublishError, match="OUTBOX_EVENT_TYPE_UNSUPPORTED") as error:
            await publisher.publish(event(event_type))
        assert not isinstance(error.value, TransientPublishError)


@pytest.mark.asyncio
async def test_broker_outages_are_transient_and_other_failures_are_not_retried() -> None:
    application = create_celery_app(settings())
    publisher = CeleryOutboxPublisher(application)

    def failing(error: Exception) -> None:
        def send(*_args: object, **_kwargs: object) -> None:
            raise error

        application.send_task = send

    for outage in (OperationalError("broker down"), OSError("connection reset"), TimeoutError()):
        failing(outage)
        with pytest.raises(TransientPublishError, match="CELERY_ENQUEUE_UNAVAILABLE"):
            await publisher.publish(event("media.ingest.requested"))

    failing(TypeError("unserializable message"))
    with pytest.raises(PublishError, match="CELERY_ENQUEUE_REJECTED") as error:
        await publisher.publish(event("media.ingest.requested"))
    assert not isinstance(error.value, TransientPublishError)


def test_every_emitted_outbox_event_type_is_classified() -> None:
    """A new event type must be routed or explicitly notification-only, never unclassified."""

    app_root = Path(__file__).resolve().parents[2] / "app"
    emitted = {
        match.group(1)
        for path in app_root.rglob("*.py")
        for match in re.finditer(r'event_type="([^"]+)"', path.read_text(encoding="utf-8"))
    }
    assert emitted, "no outbox event types were discovered"
    assert emitted <= set(DRAIN_TASK_BY_EVENT) | NOTIFICATION_ONLY_EVENTS


def test_drain_and_notification_event_sets_are_disjoint() -> None:
    assert not set(DRAIN_TASK_BY_EVENT) & NOTIFICATION_ONLY_EVENTS


def test_beat_schedule_covers_dispatch_every_drain_and_recovery() -> None:
    application = create_celery_app(
        settings(
            celery_beat_outbox_interval_seconds=11,
            celery_beat_media_drain_interval_seconds=22,
            celery_beat_recovery_interval_seconds=33,
        )
    )
    schedule = application.conf.beat_schedule
    assert {name: entry["task"] for name, entry in schedule.items()} == {
        "dispatch-outbox": "operations.outbox.dispatch",
        "drain-ingest": "media.ingest.drain",
        "drain-technical": "media.technical_analysis.drain",
        "drain-scene-speech": "media.scene_speech_analysis.drain",
        "drain-video-understanding": "media.video_understanding.drain",
        "drain-content-render": "content.render.drain",
        "drain-content-qc": "content.qc.drain",
        "recover-stale-jobs": "operations.recovery.drain",
    }
    assert schedule["dispatch-outbox"]["schedule"] == 11
    assert schedule["recover-stale-jobs"]["schedule"] == 33
    assert {
        schedule[name]["schedule"]
        for name in schedule
        if name not in {"dispatch-outbox", "recover-stale-jobs"}
    } == {22}


def test_beat_schedule_wakes_every_drain_task_the_publisher_can_route() -> None:
    schedule = create_celery_app(settings()).conf.beat_schedule
    scheduled = {entry["task"] for entry in schedule.values()}
    assert set(DRAIN_TASK_BY_EVENT.values()) <= scheduled


def test_automatic_qc_is_the_one_drain_with_no_event_behind_it() -> None:
    """A beat entry with no outbox route is a claim, so it is asserted rather than assumed.

    Every other drain is woken twice: by the event its producer wrote and by the beat tick that
    sweeps up anything the broker lost. QC has no producer — nothing in the render path writes a
    `content.qc.requested` event — so the tick is the whole trigger, and its claim is a scan for
    a succeeded render carrying no report. If a later slice adds the event, this test is the
    thing that fails and asks whether the scan should stay.
    """

    scheduled = {
        entry["task"] for entry in create_celery_app(settings()).conf.beat_schedule.values()
    }
    assert "content.qc.drain" in scheduled
    assert "content.qc.drain" not in set(DRAIN_TASK_BY_EVENT.values())
    assert not any(name.startswith("content.qc") for name in DRAIN_TASK_BY_EVENT)
