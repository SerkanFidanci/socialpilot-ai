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
    MEDIA_READ = "media.read"
    MEDIA_UPLOAD = "media.upload"


ROLE_PERMISSIONS: dict[BusinessRole, frozenset[Permission]] = {
    BusinessRole.OWNER: frozenset(Permission),
    BusinessRole.ADMIN: frozenset(
        {
            Permission.BUSINESS_READ,
            Permission.BUSINESS_UPDATE,
            Permission.MEMBERS_READ,
            Permission.MEMBERS_CREATE,
            Permission.MEMBERS_UPDATE,
            Permission.MEDIA_READ,
            Permission.MEDIA_UPLOAD,
        }
    ),
    BusinessRole.EDITOR: frozenset(
        {Permission.BUSINESS_READ, Permission.MEDIA_READ, Permission.MEDIA_UPLOAD}
    ),
    BusinessRole.VIEWER: frozenset({Permission.BUSINESS_READ, Permission.MEDIA_READ}),
    # Approver holds no permission yet: the approval sources it would read and decide on do not
    # exist until Phase 2. It is mapped explicitly (not omitted) so `permits` never raises for a
    # real role, and so that granting it any ability later is a deliberate one-line change here.
    BusinessRole.APPROVER: frozenset(),
}


def permits(role: BusinessRole, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS[role]
