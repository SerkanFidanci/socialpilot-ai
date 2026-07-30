"""Content actions mapped onto the central business permission table.

Like the brands module, this file owns no second role→permission table. `businesses.policy` is
the only authority; what lives here is the mapping from a named content action to the
permission it already requires.

The resulting matrix (PRD §4) differs from brands on purpose: producing content is
`business.update`, but so is editing a timeline, because a parametric edit changes what the
business will publish about itself. Reading is `business.read` for every role.
"""

from __future__ import annotations

from enum import StrEnum

from app.modules.businesses.models import BusinessRole
from app.modules.businesses.policy import Permission, permits


class ContentAction(StrEnum):
    TIMELINE_READ = "content.timeline.read"
    TIMELINE_WRITE = "content.timeline.write"
    RENDER_READ = "content.render.read"
    RENDER_REQUEST = "content.render.request"


ACTION_PERMISSIONS: dict[ContentAction, Permission] = {
    ContentAction.TIMELINE_READ: Permission.BUSINESS_READ,
    ContentAction.TIMELINE_WRITE: Permission.BUSINESS_UPDATE,
    ContentAction.RENDER_READ: Permission.BUSINESS_READ,
    ContentAction.RENDER_REQUEST: Permission.BUSINESS_UPDATE,
}


def required_permission(action: ContentAction) -> Permission:
    return ACTION_PERMISSIONS[action]


def permits_action(role: BusinessRole, action: ContentAction) -> bool:
    """Answer through the central table so a policy change stays a one-file change."""

    return permits(role, required_permission(action))
