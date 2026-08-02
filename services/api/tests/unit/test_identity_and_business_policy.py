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


def test_every_role_is_mapped_in_the_permission_table() -> None:
    """A new role must be given a permission set deliberately, not KeyError at first use."""

    for role in BusinessRole:
        assert isinstance(permits(role, Permission.BUSINESS_READ), bool)


def test_approver_holds_exactly_the_ability_to_decide() -> None:
    """Slice 2F is the phase the approver role was waiting for — and it gets one ability.

    The danger was never creating the role; it is creating it and silently granting breadth.
    Until PRD §21's approval sources existed the role held nothing at all, and the test that
    pinned that has become this one: the approver may read the business and decide whether
    content goes out, and everything else in the catalogue is still denied. Producing content in
    particular — an approver signs, it does not write.
    """

    allowed = {Permission.BUSINESS_READ, Permission.CONTENT_APPROVE}
    for permission in Permission:
        assert permits(BusinessRole.APPROVER, permission) is (permission in allowed)
    assert not permits(BusinessRole.APPROVER, Permission.CONTENT_GENERATE)


def test_only_the_approver_line_may_decide_and_only_producers_may_generate() -> None:
    """PRD §4's two lines, checked against each other rather than one at a time.

    Owner and admin hold both because they supervise; editor produces and cannot sign; approver
    signs and cannot produce; viewer does neither. Written as one table so that widening either
    permission has to be a deliberate edit here, not a side effect somewhere else.
    """

    expected = {
        BusinessRole.OWNER: (True, True),
        BusinessRole.ADMIN: (True, True),
        BusinessRole.EDITOR: (True, False),
        BusinessRole.APPROVER: (False, True),
        BusinessRole.VIEWER: (False, False),
    }
    for role, (generates, approves) in expected.items():
        assert permits(role, Permission.CONTENT_GENERATE) is generates
        assert permits(role, Permission.CONTENT_APPROVE) is approves
