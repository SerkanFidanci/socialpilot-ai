"""Content actions mapped onto the central business permission table.

Like the brands module, this file owns no second role→permission table. `businesses.policy` is
the only authority; what lives here is the mapping from a named content action to the
permission it already requires.

Every write in this module is `content.generate` (PRD §4). Reading is `business.read` for every
role.

W11 originally bound timeline writes to `business.update` on the reasoning that a parametric
edit changes what the business will publish about itself. W13 then added `content.generate` for
script generation, because PRD §4 gives an editor "içerik üretir" while withholding every other
write from that role. The two together produced a matrix nobody would have designed: an editor
could write the script and could not lay it on a timeline or ask for a render. W14 aligned them.

The line the matrix actually draws is between *producing content* and *changing the business*.
Authoring a timeline, editing it, and asking for a render are all the first thing — they create
a revision of a draft, and nothing they touch is a business setting, a membership, or a price.
An approver still holds no permission at all, so it can do none of this.
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
    SCRIPT_READ = "content.script.read"
    SCRIPT_GENERATE = "content.script.generate"
    VOICEOVER_READ = "content.voiceover.read"
    VOICEOVER_GENERATE = "content.voiceover.generate"
    PROJECT_READ = "content.project.read"
    PROJECT_WRITE = "content.project.write"
    PROJECT_DECIDE = "content.project.decide"


ACTION_PERMISSIONS: dict[ContentAction, Permission] = {
    ContentAction.TIMELINE_READ: Permission.BUSINESS_READ,
    ContentAction.TIMELINE_WRITE: Permission.CONTENT_GENERATE,
    ContentAction.RENDER_READ: Permission.BUSINESS_READ,
    ContentAction.RENDER_REQUEST: Permission.CONTENT_GENERATE,
    ContentAction.SCRIPT_READ: Permission.BUSINESS_READ,
    ContentAction.SCRIPT_GENERATE: Permission.CONTENT_GENERATE,
    # Producing a voiceover is producing content, not changing the business — same line the rest
    # of this table draws, and the same one PRD §4 draws when it gives an editor "içerik üretir".
    ContentAction.VOICEOVER_READ: Permission.BUSINESS_READ,
    ContentAction.VOICEOVER_GENERATE: Permission.CONTENT_GENERATE,
    # A project orders the writes above and produces nothing the individual actions do not. It
    # therefore sits on the same line rather than earning a permission of its own: an editor who
    # may write a script, lay a timeline and ask for a render may also ask for all three at once.
    ContentAction.PROJECT_READ: Permission.BUSINESS_READ,
    ContentAction.PROJECT_WRITE: Permission.CONTENT_GENERATE,
    # Approving and rejecting is the one content action that is *not* producing content, so it
    # is the one that does not map onto `content.generate`. This is the line PRD §4 draws: an
    # editor writes and an approver signs, and the two roles hold disjoint abilities. Requesting
    # a revision and cancelling stay on `PROJECT_WRITE` — both ask for work to be done or undone,
    # which is the producer's side of that line.
    ContentAction.PROJECT_DECIDE: Permission.CONTENT_APPROVE,
}


def required_permission(action: ContentAction) -> Permission:
    return ACTION_PERMISSIONS[action]


def permits_action(role: BusinessRole, action: ContentAction) -> bool:
    """Answer through the central table so a policy change stays a one-file change."""

    return permits(role, required_permission(action))
