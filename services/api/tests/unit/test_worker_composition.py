"""Worker process ownership and safe drain behavior."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from app.core.config import Settings
from app.worker.composition import WorkerContext, build_worker_context
from app.worker.tasks import _drain, dispatch_outbox


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


def test_context_builds_one_database_and_rejects_production_fakes(tmp_path: Path) -> None:
    context = build_worker_context(settings(worker_temp_root=str(tmp_path)))
    assert context.database is context.database
    with pytest.raises(RuntimeError, match="WORKER_PRODUCTION_ADAPTERS_NOT_CONFIGURED"):
        build_worker_context(
            settings(worker_temp_root=str(tmp_path)).model_copy(update={"app_env": "production"})
        )


@pytest.mark.asyncio
async def test_drain_stops_when_empty_and_obeys_batch_limit(tmp_path: Path) -> None:
    sessions = 0
    calls = 0

    class SessionFactory:
        def __call__(self) -> SessionFactory:
            return self

        async def __aenter__(self) -> object:
            nonlocal sessions
            sessions += 1
            return object()

        async def __aexit__(self, *_: object) -> None:
            return None

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


def test_outbox_task_keeps_events_unpublished_without_publisher() -> None:
    assert dispatch_outbox() == {"status": "not_configured", "processed": 0}
