"""Global identity repository; it deliberately has no tenant operations."""

from __future__ import annotations

from typing import cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.modules.identity.models import ExternalIdentity, User


class IdentityRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_external_identity(self, provider: str, subject: str) -> ExternalIdentity | None:
        statement = (
            select(ExternalIdentity)
            .options(selectinload(ExternalIdentity.user))
            .where(
                ExternalIdentity.provider == provider,
                ExternalIdentity.provider_subject == subject,
            )
        )
        return cast(ExternalIdentity | None, await self._session.scalar(statement))

    async def find_user_by_email(self, email: str) -> User | None:
        return cast(
            User | None, await self._session.scalar(select(User).where(User.email == email))
        )

    def add_user(self, user: User) -> None:
        self._session.add(user)

    def add_external_identity(self, identity: ExternalIdentity) -> None:
        self._session.add(identity)
