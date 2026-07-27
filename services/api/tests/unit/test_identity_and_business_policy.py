"""Unit tests for local identity verification, normalization, roles, and slugs."""

from __future__ import annotations

import time

import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import Settings
from app.core.errors import ProblemException
from app.infrastructure.identity.local import LocalIdentityTokenError, LocalIdentityVerifier
from app.modules.businesses.models import BusinessRole
from app.modules.businesses.policy import Permission, permits
from app.modules.businesses.service import create_slug
from app.modules.identity.service import normalize_email


@pytest.mark.asyncio
async def test_local_identity_token_is_signed_and_verified() -> None:
    settings = Settings(
        app_env="test",
        database_url="postgresql+asyncpg://test:test@localhost:5432/test",
        redis_url="redis://localhost:6379/0",
        celery_broker_url="redis://localhost:6379/1",
        celery_result_backend="redis://localhost:6379/2",
        local_identity_signing_key=SecretStr("test-local-identity-signing-key-123"),
    )
    token = LocalIdentityVerifier.sign_for_testing(
        signing_key="test-local-identity-signing-key-123",
        subject="subject-a",
        email="A@example.com",
    )

    identity = await LocalIdentityVerifier(settings).verify(token)

    assert identity.subject == "subject-a"
    assert identity.email == "A@example.com"


@pytest.mark.asyncio
async def test_local_identity_token_rejects_tampering() -> None:
    settings = Settings(
        app_env="test",
        database_url="postgresql+asyncpg://test:test@localhost:5432/test",
        redis_url="redis://localhost:6379/0",
        celery_broker_url="redis://localhost:6379/1",
        celery_result_backend="redis://localhost:6379/2",
        local_identity_signing_key=SecretStr("test-local-identity-signing-key-123"),
    )
    with pytest.raises(LocalIdentityTokenError):
        await LocalIdentityVerifier(settings).verify("local.v1.claims.tampered")


@pytest.mark.asyncio
async def test_local_identity_token_rejects_expiry() -> None:
    settings = Settings(
        app_env="test",
        database_url="postgresql+asyncpg://test:test@localhost:5432/test",
        redis_url="redis://localhost:6379/0",
        celery_broker_url="redis://localhost:6379/1",
        celery_result_backend="redis://localhost:6379/2",
        local_identity_signing_key=SecretStr("test-local-identity-signing-key-123"),
    )
    token = LocalIdentityVerifier.sign_for_testing(
        signing_key="test-local-identity-signing-key-123",
        subject="expired-subject",
        email="expired@example.com",
        expires_at=int(time.time()) - 1,
    )

    with pytest.raises(LocalIdentityTokenError):
        await LocalIdentityVerifier(settings).verify(token)


def test_local_identity_adapter_is_rejected_in_production() -> None:
    with pytest.raises(ValidationError):
        Settings(
            app_env="production",
            database_url="postgresql+asyncpg://user:password@db:5432/socialpilot",
            redis_url="redis://redis:6379/0",
            celery_broker_url="redis://redis:6379/1",
            celery_result_backend="redis://redis:6379/2",
            local_identity_signing_key=SecretStr("test-local-identity-signing-key-123"),
        )


def test_email_normalization_and_slug_generation() -> None:
    assert normalize_email("  Owner@Example.COM ") == "owner@example.com"
    with pytest.raises(ProblemException, match="letters or numbers"):
        create_slug("***")
    with pytest.raises(ProblemException, match="could not be accepted"):
        normalize_email("t\u00fcrk@example.com")
    assert create_slug("Acme Coffee Şube") == "acme-coffee-sube"


def test_role_permission_matrix() -> None:
    assert permits(BusinessRole.OWNER, Permission.BUSINESS_ARCHIVE)
    assert permits(BusinessRole.ADMIN, Permission.MEMBERS_CREATE)
    assert not permits(BusinessRole.EDITOR, Permission.BUSINESS_UPDATE)
    assert not permits(BusinessRole.VIEWER, Permission.MEMBERS_READ)
