"""Planner actions mapped onto the central business permission table.

No second role→permission table lives here; `businesses.policy` remains the only authority.

The line this table draws is the one PRD §4 already draws, and it lands on the *other* side from
`content`'s. Configuring the planner is not producing content — it is saying what the business
wants published and when it is willing to publish, which is a business setting in exactly the
sense a rename or a timezone is. So it is `business.update`, and an editor (who produces content
and changes nothing about the business) cannot rewrite the schedule the whole tenant runs on.

What the planner *does* with that configuration is produce content, and that happens through
`ContentProjectService.create_project` with its own `content.generate` check, acting as the
person who set the standing demand up. There is deliberately no permission called
"planner.generate": a second way to reach the same table would be a weaker way.
"""

from __future__ import annotations

from enum import StrEnum

from app.modules.businesses.models import BusinessRole
from app.modules.businesses.policy import Permission, permits


class PlannerAction(StrEnum):
    SETTINGS_READ = "planner.settings.read"
    SETTINGS_WRITE = "planner.settings.write"
    ITEM_READ = "planner.item.read"
    ITEM_WRITE = "planner.item.write"
    OBLIGATION_READ = "planner.obligation.read"
    # Withdrawing a planned obligation, or bringing a blocked one back. Both change what the
    # business will publish, so both sit beside the configuration rather than beside generation.
    OBLIGATION_WRITE = "planner.obligation.write"
    PLAN_READ = "planner.plan.read"


ACTION_PERMISSIONS: dict[PlannerAction, Permission] = {
    PlannerAction.SETTINGS_READ: Permission.BUSINESS_READ,
    PlannerAction.SETTINGS_WRITE: Permission.BUSINESS_UPDATE,
    PlannerAction.ITEM_READ: Permission.BUSINESS_READ,
    PlannerAction.ITEM_WRITE: Permission.BUSINESS_UPDATE,
    PlannerAction.OBLIGATION_READ: Permission.BUSINESS_READ,
    PlannerAction.OBLIGATION_WRITE: Permission.BUSINESS_UPDATE,
    PlannerAction.PLAN_READ: Permission.BUSINESS_READ,
}

_UNMAPPED = tuple(action.value for action in PlannerAction if action not in ACTION_PERMISSIONS)
if _UNMAPPED:  # pragma: no cover - a start-up failure, asserted by the unit suite
    raise RuntimeError(f"planner actions with no permission: {_UNMAPPED}")


def required_permission(action: PlannerAction) -> Permission:
    return ACTION_PERMISSIONS[action]


def permits_action(role: BusinessRole, action: PlannerAction) -> bool:
    """Answer through the central table so a policy change stays a one-file change."""

    return permits(role, required_permission(action))


__all__ = [
    "ACTION_PERMISSIONS",
    "PlannerAction",
    "permits_action",
    "required_permission",
]
