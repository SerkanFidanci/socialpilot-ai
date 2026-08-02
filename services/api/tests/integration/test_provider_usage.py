"""PostgreSQL integration coverage for provider_usage persistence (W10, item 1).

The benchmark harness is offline and DB-free, so it never writes here on its own. This suite
proves the other half of the ADR-007 contract: the records a benchmark run produces persist to
``provider_usage`` under a tenant, round-trip intact, stay tenant-scoped, and carry none of the
excluded payloads (there is no column for a token, prompt, signed URL or raw response).
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Generator
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.benchmark import load_samples, run_benchmark
from app.benchmark.providers import build_fake_registry
from app.core.config import get_settings
from app.modules.businesses.models import Business, BusinessStatus
from app.modules.identity.models import User, UserStatus
from app.modules.operations.models import ProviderUsage

pytestmark = pytest.mark.integration


async def clear() -> None:
    engine = create_async_engine(os.environ["DATABASE_URL"])
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "TRUNCATE credit_ledger, usage_reservations, provider_usage, businesses, users CASCADE"
                )
            )
    finally:
        await engine.dispose()


@pytest.fixture(autouse=True)
def clean() -> Generator[None]:
    if os.getenv("RUN_INTEGRATION_TESTS") == "1":
        asyncio.run(clear())
    yield
    if os.getenv("RUN_INTEGRATION_TESTS") == "1":
        asyncio.run(clear())


async def _make_business(session_factory: async_sessionmaker[AsyncSession], slug: str) -> UUID:
    user_id, business_id = uuid4(), uuid4()
    async with session_factory() as session:
        async with session.begin():
            session.add(User(id=user_id, email=f"{slug}@example.com", status=UserStatus.ACTIVE))
            # Flush the owner before the business so its created_by_user_id FK resolves.
            await session.flush()
            session.add(
                Business(
                    id=business_id,
                    name=slug,
                    slug=slug,
                    status=BusinessStatus.ACTIVE,
                    timezone="Europe/Istanbul",
                    created_by_user_id=user_id,
                )
            )
    return business_id


@pytest.mark.skipif(os.getenv("RUN_INTEGRATION_TESTS") != "1", reason="requires PostgreSQL")
def test_benchmark_records_persist_under_a_tenant_and_stay_scoped() -> None:
    settings = get_settings()
    engine = create_async_engine(os.environ["DATABASE_URL"])
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def run() -> None:
        try:
            tenant = await _make_business(session_factory, "usage-tenant")
            other = await _make_business(session_factory, "other-tenant")

            report = run_benchmark(
                registry=build_fake_registry(), samples=load_samples(), settings=settings
            )
            assert report.usage, "the benchmark produced no usage records to persist"

            async with session_factory() as session:
                async with session.begin():
                    for record in report.usage:
                        session.add(
                            ProviderUsage.from_measurement(
                                business_id=tenant,
                                capability=record.capability.value,
                                provider=record.provider,
                                model=record.model,
                                estimated_cost_minor=record.estimated_cost_minor,
                                actual_cost_minor=record.actual_cost_minor,
                                currency=record.currency,
                                duration_ms=record.duration_ms,
                                outcome=record.outcome,
                                correlation_id=record.correlation_id,
                                run_id="benchmark-run",
                            )
                        )

            async with session_factory() as session:
                stored = list(
                    (
                        await session.scalars(
                            select(ProviderUsage).where(ProviderUsage.business_id == tenant)
                        )
                    ).all()
                )
                # Every produced record persisted, and a sample round-trips its measurement.
                assert len(stored) == len(report.usage)
                sample = report.usage[0]
                match = next(row for row in stored if row.correlation_id == sample.correlation_id)
                assert match.capability == sample.capability.value
                assert match.provider == sample.provider
                assert match.estimated_cost_minor == sample.estimated_cost_minor
                assert match.actual_cost_minor == sample.actual_cost_minor
                assert match.currency == sample.currency
                assert match.job_id is None and match.asset_id is None
                assert match.run_id == "benchmark-run"

                # A second tenant sees none of the first tenant's usage.
                other_count = await session.scalar(
                    select(func.count())
                    .select_from(ProviderUsage)
                    .where(ProviderUsage.business_id == other)
                )
                assert other_count == 0
        finally:
            await engine.dispose()

    asyncio.run(run())


@pytest.mark.skipif(os.getenv("RUN_INTEGRATION_TESTS") != "1", reason="requires PostgreSQL")
def test_provider_usage_has_no_column_for_an_excluded_payload() -> None:
    columns = set(ProviderUsage.__table__.columns.keys())
    for forbidden in ("token", "prompt", "url", "response", "signature", "payload", "secret"):
        assert not any(forbidden in name for name in columns), (forbidden, columns)
