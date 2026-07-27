"""Business and membership application services with tenant enforcement."""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ProblemException
from app.modules.businesses.models import (
    Business,
    BusinessMember,
    BusinessRole,
    BusinessStatus,
    MembershipStatus,
)
from app.modules.businesses.policy import Permission, permits
from app.modules.businesses.repository import BusinessRepository
from app.modules.identity.service import normalize_email


def create_slug(value: str) -> str:
    """Generate a normalized, display-only slug; UUID remains the tenant key."""

    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")
    if not slug:
        raise ProblemException(
            status=400,
            code="REQUEST_INVALID",
            title="Invalid request",
            detail="Business name must contain letters or numbers.",
        )
    return slug[:150]


class BusinessService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repository = BusinessRepository(session)

    async def create_business(self, *, user_id: UUID, name: str, timezone: str) -> Business:
        self._validate_timezone(timezone)
        cleaned_name = name.strip()
        if not cleaned_name:
            raise ProblemException(
                status=400,
                code="REQUEST_INVALID",
                title="Invalid request",
                detail="Business name is required.",
            )
        async with self._session.begin():
            for _ in range(10):
                slug = await self._next_slug(cleaned_name)
                try:
                    async with self._session.begin_nested():
                        business = Business(
                            name=cleaned_name,
                            slug=slug,
                            timezone=timezone,
                            status=BusinessStatus.ACTIVE,
                            created_by_user_id=user_id,
                        )
                        self._repository.add_business(business)
                        await self._session.flush()
                        owner = BusinessMember(
                            business_id=business.id,
                            user_id=user_id,
                            role=BusinessRole.OWNER,
                            status=MembershipStatus.ACTIVE,
                            joined_at=datetime.now(UTC),
                        )
                        self._repository.add_member(owner)
                        await self._session.flush()
                        return business
                except IntegrityError:
                    continue
        raise ProblemException(
            status=409,
            code="BUSINESS_SLUG_CONFLICT",
            title="Business slug conflict",
            detail="The business could not be named safely.",
        )

    async def list_businesses(self, user_id: UUID) -> list[Business]:
        return await self._repository.list_for_active_user(user_id)

    async def get_business(self, *, user_id: UUID, business_id: UUID) -> Business:
        await self._authorized_membership(user_id, business_id, Permission.BUSINESS_READ)
        business = await self._repository.get_business(business_id)
        if business is None:
            raise self._business_not_found()
        return business

    async def update_business(
        self,
        *,
        user_id: UUID,
        business_id: UUID,
        name: str | None,
        timezone: str | None,
        status: BusinessStatus | None,
    ) -> Business:
        permission = (
            Permission.BUSINESS_ARCHIVE
            if status == BusinessStatus.ARCHIVED
            else Permission.BUSINESS_UPDATE
        )
        async with self._session.begin():
            await self._authorized_membership(user_id, business_id, permission)
            business = await self._require_active_business(business_id)
            if name is not None:
                cleaned_name = name.strip()
                if not cleaned_name:
                    raise ProblemException(
                        status=400,
                        code="REQUEST_INVALID",
                        title="Invalid request",
                        detail="Business name is required.",
                    )
                business.name = cleaned_name
            if timezone is not None:
                self._validate_timezone(timezone)
                business.timezone = timezone
            if status is not None:
                business.status = status
            await self._session.flush()
            return business

    async def list_members(self, *, user_id: UUID, business_id: UUID) -> list[BusinessMember]:
        await self._authorized_membership(user_id, business_id, Permission.MEMBERS_READ)
        return await self._repository.list_members(business_id)

    async def add_member(
        self, *, user_id: UUID, business_id: UUID, email: str, role: BusinessRole
    ) -> BusinessMember:
        async with self._session.begin():
            actor = await self._authorized_membership(
                user_id, business_id, Permission.MEMBERS_CREATE
            )
            await self._require_active_business(business_id)
            target_user = await self._repository.get_user_by_email(normalize_email(email))
            if target_user is None:
                raise ProblemException(
                    status=404,
                    code="USER_NOT_FOUND",
                    title="User not found",
                    detail="The user is not available.",
                )
            existing_member = await self._repository.get_membership(business_id, target_user.id)
            self._assert_role_assignment(actor, existing_member, role)
            if existing_member is not None and existing_member.status == MembershipStatus.ACTIVE:
                raise ProblemException(
                    status=409,
                    code="MEMBER_ALREADY_EXISTS",
                    title="Member already exists",
                    detail="The user is already an active member.",
                )
            if existing_member is None:
                member = BusinessMember(
                    business_id=business_id,
                    user_id=target_user.id,
                    role=role,
                    status=MembershipStatus.ACTIVE,
                    joined_at=datetime.now(UTC),
                )
                self._repository.add_member(member)
            else:
                member = existing_member
                member.role = role
                member.status = MembershipStatus.ACTIVE
                member.joined_at = datetime.now(UTC)
            await self._session.flush()
            return member

    async def update_member(
        self,
        *,
        user_id: UUID,
        business_id: UUID,
        member_id: UUID,
        role: BusinessRole | None,
        status: MembershipStatus | None,
    ) -> BusinessMember:
        async with self._session.begin():
            actor = await self._authorized_membership(
                user_id, business_id, Permission.MEMBERS_UPDATE
            )
            await self._require_active_business(business_id)
            active_owners = await self._repository.lock_active_owners(business_id)
            member = await self._repository.get_member_for_update(business_id, member_id)
            if member is None:
                raise ProblemException(
                    status=404,
                    code="MEMBER_NOT_FOUND",
                    title="Member not found",
                    detail="The member is not available.",
                )
            if role is None and status is None:
                raise ProblemException(
                    status=400,
                    code="REQUEST_INVALID",
                    title="Invalid request",
                    detail="No member change was supplied.",
                )
            self._assert_role_assignment(actor, member, role)
            removal_or_demotion = member.role == BusinessRole.OWNER and (
                (role is not None and role != BusinessRole.OWNER)
                or (status is not None and status != MembershipStatus.ACTIVE)
            )
            if removal_or_demotion and len(active_owners) <= 1:
                raise ProblemException(
                    status=409,
                    code="LAST_OWNER_REQUIRED",
                    title="Owner required",
                    detail="A business must retain an active owner.",
                )
            if role is not None:
                member.role = role
            if status is not None:
                member.status = status
            await self._session.flush()
            return member

    async def _authorized_membership(
        self, user_id: UUID, business_id: UUID, permission: Permission
    ) -> BusinessMember:
        membership = await self._repository.get_active_membership(business_id, user_id)
        if membership is None:
            raise self._business_not_found()
        if not permits(membership.role, permission):
            raise ProblemException(
                status=403,
                code="INSUFFICIENT_PERMISSION",
                title="Forbidden",
                detail="You do not have this permission.",
            )
        return membership

    async def _next_slug(self, name: str) -> str:
        base = create_slug(name)
        if not await self._repository.slug_exists(base):
            return base
        for _ in range(20):
            candidate = f"{base[:143]}-{uuid4().hex[:6]}"
            if not await self._repository.slug_exists(candidate):
                return candidate
        raise ProblemException(
            status=409,
            code="BUSINESS_SLUG_CONFLICT",
            title="Business slug conflict",
            detail="The business could not be named safely.",
        )

    async def _require_active_business(self, business_id: UUID) -> Business:
        business = await self._repository.get_business(business_id)
        if business is None:
            raise self._business_not_found()
        if business.status != BusinessStatus.ACTIVE:
            raise ProblemException(
                status=409,
                code="BUSINESS_NOT_MUTABLE",
                title="Business is not mutable",
                detail="Suspended or archived businesses cannot be changed.",
            )
        return business

    @staticmethod
    def _validate_timezone(value: str) -> None:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as error:
            raise ProblemException(
                status=400,
                code="REQUEST_INVALID",
                title="Invalid request",
                detail="Timezone is invalid.",
            ) from error

    @staticmethod
    def _business_not_found() -> ProblemException:
        return ProblemException(
            status=404,
            code="BUSINESS_NOT_FOUND",
            title="Business not found",
            detail="The business is not available.",
        )

    @staticmethod
    def _assert_role_assignment(
        actor: BusinessMember, target: BusinessMember | None, requested_role: BusinessRole | None
    ) -> None:
        if actor.role == BusinessRole.ADMIN and (
            (requested_role == BusinessRole.OWNER)
            or (target is not None and target.role == BusinessRole.OWNER)
        ):
            raise ProblemException(
                status=403,
                code="INVALID_ROLE_CHANGE",
                title="Forbidden",
                detail="Administrators cannot change owner memberships.",
            )
