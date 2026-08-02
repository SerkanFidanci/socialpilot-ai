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
    # Producing content is its own permission because PRD §4 gives it to a role that holds no
    # other write: an editor "içerik üretir" but cannot change the business, and an approver can
    # do neither. Folding generation into `business.update` would lock editors out; folding it
    # into `media.upload` would make the table lie about what the permission means.
    CONTENT_GENERATE = "content.generate"
    # Deciding whether produced content may go out (PRD §21). Separate from `content.generate`
    # because PRD §4 makes them separate roles: an approver holds *only* this one and cannot
    # produce anything, and an editor produces content and cannot sign it off. Folding approval
    # into generation would give every editor the approver's signature and leave the approver
    # role with nothing to do — which is what it had before slice 2F.
    CONTENT_APPROVE = "content.approve"
    # Writing credits into a tenant's ledger. Held by the owner alone — not by an admin, who may
    # otherwise do everything an owner can except end the business. The reason is the same one:
    # a grant is money, and until Phase 3 connects a store there is no receipt behind it, so the
    # only hand that should be able to create one is the hand that pays. Spending credits needs
    # no permission of its own; it happens as a side effect of `content.generate`.
    ENTITLEMENT_GRANT = "entitlement.grant"


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
            Permission.CONTENT_GENERATE,
            Permission.CONTENT_APPROVE,
        }
    ),
    # An editor produces content and does not sign it off (PRD §4). It may ask for a revision —
    # that is a generation request, and it goes through `content.generate`.
    BusinessRole.EDITOR: frozenset(
        {
            Permission.BUSINESS_READ,
            Permission.MEDIA_READ,
            Permission.MEDIA_UPLOAD,
            Permission.CONTENT_GENERATE,
        }
    ),
    BusinessRole.VIEWER: frozenset({Permission.BUSINESS_READ, Permission.MEDIA_READ}),
    # Slice 2F is the phase this role was waiting for. It holds exactly one ability — deciding
    # whether produced content may go out — and still cannot produce, upload or configure
    # anything. `business.read` comes with it because a decision made without seeing the project
    # would not be a decision.
    #
    # There is deliberately **no self-approval restriction**: a business with one owner and one
    # approver would deadlock the moment the approver was also the person who asked for the
    # content, and PRD §4 does not ask for separation of duties. Who approved what is recorded on
    # every decision, so the question stays answerable without being enforced.
    BusinessRole.APPROVER: frozenset({Permission.BUSINESS_READ, Permission.CONTENT_APPROVE}),
}


def permits(role: BusinessRole, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS[role]
