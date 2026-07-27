"""Tenant-scoped business persistence operations."""

from __future__ import annotations

from typing import cast
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.businesses.models import Business, BusinessMember, BusinessRole, MembershipStatus
from app.modules.identity.models import User


class BusinessRepository:
    """All tenant resource lookups require an explicit business id."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def slug_exists(self, slug: str) -> bool:
        return (
            await self._session.scalar(select(Business.id).where(Business.slug == slug)) is not None
        )

    async def list_for_active_user(self, user_id: UUID) -> list[Business]:
        statement: Select[tuple[Business]] = (
            select(Business)
            .join(BusinessMember, BusinessMember.business_id == Business.id)
            .where(
                BusinessMember.user_id == user_id, BusinessMember.status == MembershipStatus.ACTIVE
            )
            .order_by(Business.created_at)
        )
        return list((await self._session.scalars(statement)).all())

    async def get_business(self, business_id: UUID) -> Business | None:
        return await self._session.get(Business, business_id)

    async def get_active_membership(
        self, business_id: UUID, user_id: UUID
    ) -> BusinessMember | None:
        statement = select(BusinessMember).where(
            BusinessMember.business_id == business_id,
            BusinessMember.user_id == user_id,
            BusinessMember.status == MembershipStatus.ACTIVE,
        )
        return cast(BusinessMember | None, await self._session.scalar(statement))

    async def get_membership(self, business_id: UUID, user_id: UUID) -> BusinessMember | None:
        statement = select(BusinessMember).where(
            BusinessMember.business_id == business_id,
            BusinessMember.user_id == user_id,
        )
        return cast(BusinessMember | None, await self._session.scalar(statement))

    async def list_members(self, business_id: UUID) -> list[BusinessMember]:
        statement: Select[tuple[BusinessMember]] = (
            select(BusinessMember)
            .where(BusinessMember.business_id == business_id)
            .order_by(BusinessMember.created_at)
        )
        return list((await self._session.scalars(statement)).all())

    async def get_member(self, business_id: UUID, member_id: UUID) -> BusinessMember | None:
        statement = select(BusinessMember).where(
            BusinessMember.business_id == business_id, BusinessMember.id == member_id
        )
        return cast(BusinessMember | None, await self._session.scalar(statement))

    async def get_member_for_update(
        self, business_id: UUID, member_id: UUID
    ) -> BusinessMember | None:
        statement = (
            select(BusinessMember)
            .where(BusinessMember.business_id == business_id, BusinessMember.id == member_id)
            .with_for_update()
        )
        return cast(BusinessMember | None, await self._session.scalar(statement))

    async def get_user_by_email(self, email: str) -> User | None:
        return cast(
            User | None, await self._session.scalar(select(User).where(User.email == email))
        )

    async def lock_active_owners(self, business_id: UUID) -> list[BusinessMember]:
        statement: Select[tuple[BusinessMember]] = (
            select(BusinessMember)
            .where(
                BusinessMember.business_id == business_id,
                BusinessMember.status == MembershipStatus.ACTIVE,
                BusinessMember.role == BusinessRole.OWNER,
            )
            .order_by(BusinessMember.id)
            .with_for_update()
        )
        return list((await self._session.scalars(statement)).all())

    def add_business(self, business: Business) -> None:
        self._session.add(business)

    def add_member(self, member: BusinessMember) -> None:
        self._session.add(member)
