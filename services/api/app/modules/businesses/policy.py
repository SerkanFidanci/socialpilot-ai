"""Central business-role permission policy."""

from __future__ import annotations

from enum import StrEnum

from app.modules.businesses.models import BusinessRole


class Permission(StrEnum):
    BUSINESS_READ = "business.read"
    BUSINESS_UPDATE = "business.update"
    BUSINESS_ARCHIVE = "business.archive"
    MEMBERS_READ = "members.read"
    MEMBERS_CREATE = "members.create"
    MEMBERS_UPDATE = "members.update"


ROLE_PERMISSIONS: dict[BusinessRole, frozenset[Permission]] = {
    BusinessRole.OWNER: frozenset(Permission),
    BusinessRole.ADMIN: frozenset(
        {
            Permission.BUSINESS_READ,
            Permission.BUSINESS_UPDATE,
            Permission.MEMBERS_READ,
            Permission.MEMBERS_CREATE,
            Permission.MEMBERS_UPDATE,
        }
    ),
    BusinessRole.EDITOR: frozenset({Permission.BUSINESS_READ}),
    BusinessRole.VIEWER: frozenset({Permission.BUSINESS_READ}),
}


def permits(role: BusinessRole, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS[role]
