"""PRD §12.4's content point table, as versioned data rather than as constants in a call site.

Three properties matter here, and each one is enforced by construction rather than by a comment.

**The table is versioned and every version is kept.** `POINT_TABLES` is a mapping from version
number to table, and old versions are never removed. A ledger entry records the version it was
priced under, so the question "what was this charged at" is answerable after the prices move —
and it has to be, because the numbers below are PRD §12.4's *illustrative* points and are still
waiting to be calibrated against the provider costs W08's benchmark measures (see `docs/STATUS.md`).
A pricing argument that cannot name the table in force on the day is not resolvable.

**Nothing recomputes an old charge.** Resolution happens once, when the reservation opens; the
resolved number is stored on the reservation and on the ledger entry. Changing
`ENTITLEMENT_POINTS_VERSION` therefore prices *new* work differently and leaves every existing
row alone. There is deliberately no function anywhere that takes a stored entry and re-derives
its credits from a table.

**The table is total over the content vocabulary.** `PointTable` refuses to exist unless it
prices every `ContentPointKind` and maps every `(ScenarioCode, RenderProfile)` pair to one. That
check runs at import time, so adding a render profile without deciding what it costs breaks the
application at start-up instead of producing an unpriced — that is, free — kind of content.

This module reads content's *vocabulary* (`ScenarioCode`, `RenderProfile`) and nothing else about
it: no table, no service, no state machine. Keeping a second copy of that vocabulary here is what
would actually be dangerous, because the two would drift and the drift would look like a price.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from app.modules.content.render import RenderProfile
from app.modules.content.script import ScenarioCode


class ContentPointKind(StrEnum):
    """PRD §12.4's rows, one member each, with the section's own names.

    The enum is the price list's key space, not the product's: several render profiles map to
    one row, because §12.4 prices a *publishing surface* rather than a geometry.
    """

    X_POST = "x_post"
    STORY = "story"
    STATIC_POST = "static_post"
    CAROUSEL = "carousel"
    STANDARD_REELS = "standard_reels"
    PROFESSIONAL_REELS = "professional_reels"
    PREMIUM_VIDEO = "premium_video"
    AD_CREATIVE_VARIATION = "ad_creative_variation"
    GENERATIVE_VIDEO_SCENE = "generative_video_scene"


class PointTableError(RuntimeError):
    """A point table that does not price everything it claims to. Raised at import time."""


@dataclass(frozen=True, slots=True)
class PointTable:
    """One immutable version of §12.4: what each kind costs, and which surface is which kind."""

    version: int
    points: dict[ContentPointKind, int]
    surfaces: dict[tuple[ScenarioCode, RenderProfile], ContentPointKind]

    def __post_init__(self) -> None:
        if self.version < 1:
            raise PointTableError("a point table version is a positive integer")
        missing_kinds = sorted(kind.value for kind in ContentPointKind if kind not in self.points)
        if missing_kinds:
            raise PointTableError(f"point table {self.version} prices no {missing_kinds}")
        if any(credits <= 0 for credits in self.points.values()):
            raise PointTableError(f"point table {self.version} has a non-positive price")
        # Totality over the product, not over the keys someone remembered to write. A new render
        # profile is a deployment failure here rather than a silently free kind of content.
        missing_surfaces = sorted(
            f"{scenario.value}/{profile.value}"
            for scenario in ScenarioCode
            for profile in RenderProfile
            if (scenario, profile) not in self.surfaces
        )
        if missing_surfaces:
            raise PointTableError(f"point table {self.version} does not map {missing_surfaces}")

    def kind_for(self, scenario: ScenarioCode, profile: RenderProfile) -> ContentPointKind:
        """Which §12.4 row a project of this shape is. Total by the constructor's check."""

        return self.surfaces[(scenario, profile)]

    def credits_for(self, scenario: ScenarioCode, profile: RenderProfile) -> int:
        """What one generation of this shape costs, in whole credits."""

        return self.points[self.kind_for(scenario, profile)]


# Version 1: PRD §12.4's example points, transcribed without adjustment.
#
# Two of §12.4's rows carry a quality tier the platform cannot express yet — "Professional Reels"
# and "Premium video" belong to §12.3's tiers, which arrive with the subscription item (§12.2
# `quality_tier`) in Phase 3. They are priced here so the table is complete on the day that lands,
# and nothing maps to them yet: every project today is the standard tier.
_V1_POINTS: Final[dict[ContentPointKind, int]] = {
    ContentPointKind.X_POST: 1,
    ContentPointKind.STORY: 1,
    ContentPointKind.STATIC_POST: 2,
    ContentPointKind.CAROUSEL: 3,
    ContentPointKind.STANDARD_REELS: 5,
    ContentPointKind.PROFESSIONAL_REELS: 8,
    ContentPointKind.PREMIUM_VIDEO: 20,
    ContentPointKind.AD_CREATIVE_VARIATION: 5,
    # §12.4 writes "10+" for a generative scene. A price list cannot hold a "+": the open end is
    # a future per-scene multiplier, and until that exists the floor is the price.
    ContentPointKind.GENERATIVE_VIDEO_SCENE: 10,
}

_V1_SURFACES: Final[dict[tuple[ScenarioCode, RenderProfile], ContentPointKind]] = {
    # The daily product reel (§14.1) is the only scenario that exists; the render profile is what
    # decides the surface, and therefore the row.
    (ScenarioCode.PRODUCT_REELS, RenderProfile.INSTAGRAM_REELS_1080X1920): (
        ContentPointKind.STANDARD_REELS
    ),
    (ScenarioCode.PRODUCT_REELS, RenderProfile.INSTAGRAM_STORY_1080X1920): ContentPointKind.STORY,
    (ScenarioCode.PRODUCT_REELS, RenderProfile.INSTAGRAM_FEED_1080X1350): (
        ContentPointKind.STATIC_POST
    ),
    (ScenarioCode.PRODUCT_REELS, RenderProfile.INSTAGRAM_SQUARE_1080X1080): (
        ContentPointKind.STATIC_POST
    ),
    (ScenarioCode.PRODUCT_REELS, RenderProfile.X_VIDEO_1280X720): ContentPointKind.X_POST,
    (ScenarioCode.PRODUCT_REELS, RenderProfile.X_VERTICAL_1080X1920): ContentPointKind.X_POST,
    # The review proxy (§15.5 applied to output) is not a publishing surface, but producing one
    # runs the same script, speech and render pipeline as the deliverable it previews. Pricing it
    # below the deliverable would make "ask for a preview profile" the cheap way to buy a
    # generation, so it costs what the reel it stands in for costs.
    (ScenarioCode.PRODUCT_REELS, RenderProfile.PREVIEW_540X960): ContentPointKind.STANDARD_REELS,
}

POINT_TABLE_V1: Final = PointTable(version=1, points=_V1_POINTS, surfaces=_V1_SURFACES)

POINT_TABLES: Final[dict[int, PointTable]] = {POINT_TABLE_V1.version: POINT_TABLE_V1}
"""Every version that has ever priced a ledger entry, newest or not. Nothing is ever removed."""


def point_table(version: int) -> PointTable:
    """The table for one version. Missing versions are a configuration error, caught at boot."""

    table = POINT_TABLES.get(version)
    if table is None:
        raise PointTableError(f"no point table is registered for version {version}")
    return table


__all__ = [
    "POINT_TABLES",
    "POINT_TABLE_V1",
    "ContentPointKind",
    "PointTable",
    "PointTableError",
    "point_table",
]
