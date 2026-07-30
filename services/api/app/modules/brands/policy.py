"""Brand and catalogue actions mapped onto the central business permission table.

This module deliberately does **not** own a second role→permission table. `businesses.policy`
is the only authority in the system, and duplicating it here would let the two drift until a
role silently gains an ability nobody granted. What lives here is the mapping from a named
brand action to the existing permission it requires, so a route or service never compares a
role by hand.

Resulting matrix (PRD §4): `owner`/`admin` hold `business.update` and therefore write brand and
catalogue data; `editor` and `viewer` hold only `business.read` and therefore cannot — an editor
uploads media and produces content but does not change what the brand claims about itself.
"""

from __future__ import annotations

from enum import StrEnum

from app.modules.businesses.models import BusinessRole
from app.modules.businesses.policy import Permission, permits


class BrandAction(StrEnum):
    BRAND_READ = "brand.read"
    BRAND_WRITE = "brand.write"
    CATALOG_READ = "catalog.read"
    CATALOG_WRITE = "catalog.write"
    CAMPAIGN_READ = "campaign.read"
    CAMPAIGN_WRITE = "campaign.write"


ACTION_PERMISSIONS: dict[BrandAction, Permission] = {
    BrandAction.BRAND_READ: Permission.BUSINESS_READ,
    BrandAction.BRAND_WRITE: Permission.BUSINESS_UPDATE,
    BrandAction.CATALOG_READ: Permission.BUSINESS_READ,
    BrandAction.CATALOG_WRITE: Permission.BUSINESS_UPDATE,
    BrandAction.CAMPAIGN_READ: Permission.BUSINESS_READ,
    BrandAction.CAMPAIGN_WRITE: Permission.BUSINESS_UPDATE,
}


def required_permission(action: BrandAction) -> Permission:
    return ACTION_PERMISSIONS[action]


def permits_action(role: BusinessRole, action: BrandAction) -> bool:
    """Answer through the central table so a policy change stays a one-file change."""

    return permits(role, required_permission(action))
