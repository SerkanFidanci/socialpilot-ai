"""Identity resolution application service."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ProblemException
from app.modules.identity.domain import VerifiedIdentity
from app.modules.identity.models import ExternalIdentity, User, UserStatus
from app.modules.identity.repository import IdentityRepository


def normalize_email(value: str) -> str:
    """Normalize an email for storage; it never authenticates an identity."""

    normalized = value.strip().lower()
    if not normalized or not normalized.isascii() or "@" not in normalized or len(normalized) > 320:
        raise ProblemException(
            status=401,
            code="INVALID_IDENTITY_TOKEN",
            title="Invalid identity token",
            detail="The identity token could not be accepted.",
        )
    return normalized


class IdentityService:
    """Resolve a verified provider subject into one internal user idempotently."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repository = IdentityRepository(session)

    async def resolve(self, verified: VerifiedIdentity) -> User:
        if (
            verified.provider != verified.provider.strip()
            or not verified.provider
            or len(verified.provider) > 64
            or verified.subject != verified.subject.strip()
            or not verified.subject
            or len(verified.subject) > 255
        ):
            raise ProblemException(
                status=401,
                code="INVALID_IDENTITY_TOKEN",
                title="Invalid identity token",
                detail="The identity token could not be accepted.",
            )
        existing = await self._repository.find_external_identity(
            verified.provider, verified.subject
        )
        if existing is not None:
            existing.last_seen_at = datetime.now(UTC)
            if existing.user.status != UserStatus.ACTIVE:
                raise ProblemException(
                    status=401,
                    code="AUTHENTICATION_REQUIRED",
                    title="Authentication required",
                    detail="The identity token could not be accepted.",
                )
            return existing.user

        email = normalize_email(verified.email)
        try:
            async with self._session.begin_nested():
                user = User(
                    email=email, display_name=verified.display_name, status=UserStatus.ACTIVE
                )
                identity = ExternalIdentity(
                    user=user,
                    provider=verified.provider,
                    provider_subject=verified.subject,
                    email_at_provider=email,
                )
                self._repository.add_user(user)
                self._repository.add_external_identity(identity)
                await self._session.flush()
                return user
        except IntegrityError:
            existing = await self._repository.find_external_identity(
                verified.provider, verified.subject
            )
            if existing is not None:
                existing.last_seen_at = datetime.now(UTC)
                return existing.user
            raise ProblemException(
                status=409,
                code="IDENTITY_CONFLICT",
                title="Identity conflict",
                detail="The identity could not be resolved.",
            ) from None
