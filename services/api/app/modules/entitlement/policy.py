"""Entitlement actions mapped onto the central business permission table.

No second role→permission table lives here, for the same reason the content and brands modules
keep none: `businesses.policy` is the only authority, and this file is the mapping from a named
action to the permission it already requires.

The line drawn is between reading a balance and creating one. Reading is `business.read`, so
anyone who can see the business can see what it has left — a viewer who cannot start a generation
still needs to know why one was refused. Creating credit is `entitlement.grant`, which only an
owner holds.

Spending is deliberately absent from this table. A reservation is opened by the operation that
needs it, under that operation's own permission, and never by a request that asks only to spend.
There is no endpoint whose effect is "take credits away", so there is nothing here to authorise.
"""

from __future__ import annotations

from enum import StrEnum

from app.modules.businesses.models import BusinessRole
from app.modules.businesses.policy import Permission, permits


class EntitlementAction(StrEnum):
    BALANCE_READ = "entitlement.balance.read"
    LEDGER_READ = "entitlement.ledger.read"
    GRANT_CREATE = "entitlement.grant.create"


ACTION_PERMISSIONS: dict[EntitlementAction, Permission] = {
    EntitlementAction.BALANCE_READ: Permission.BUSINESS_READ,
    EntitlementAction.LEDGER_READ: Permission.BUSINESS_READ,
    EntitlementAction.GRANT_CREATE: Permission.ENTITLEMENT_GRANT,
}


def required_permission(action: EntitlementAction) -> Permission:
    return ACTION_PERMISSIONS[action]


def permits_action(role: BusinessRole, action: EntitlementAction) -> bool:
    """Answer through the central table so a policy change stays a one-file change."""

    return permits(role, required_permission(action))


__all__ = [
    "ACTION_PERMISSIONS",
    "EntitlementAction",
    "permits_action",
    "required_permission",
]
