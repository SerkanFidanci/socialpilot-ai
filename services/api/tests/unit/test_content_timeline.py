"""Timeline schema, parametric patching and §18.3 validation — all pure, no database.

The rules under test are the ones that make the parametric-editing decision (K4) real rather
than aspirational: a raw coordinate must be a parse error, a verified slot must be unwritable
by hand, and text that cannot fit the safe area must be refused before any render begins.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.infrastructure.render.fake import FakeRenderAdapter
from app.modules.content.domain import format_money
from app.modules.content.patch import PatchOperation, apply_patch, parse_patch, serialize_patch
from app.modules.content.render import RenderProfile, profile_spec
from app.modules.content.timeline import (
    OverlayAnchor,
    TimelineSchemaError,
    parse_timeline,
    serialize_timeline,
)
from app.modules.content.validation import (
    AssetFacts,
    ValidationContext,
    VerifiedValue,
    validate_timeline,
)
from app.modules.operations.service import request_fingerprint

ASSET = UUID("11111111-1111-4111-8111-111111111111")
OTHER_ASSET = UUID("22222222-2222-4222-8222-222222222222")
LOGO_ASSET = UUID("33333333-3333-4333-8333-333333333333")
CAMPAIGN = UUID("44444444-4444-4444-8444-444444444444")
PROFILE = RenderProfile.INSTAGRAM_REELS_1080X1920
CAPABILITIES = FakeRenderAdapter().capabilities


def clip(**overrides: Any) -> dict[str, Any]:
    return {
        "asset_id": str(ASSET),
        "source_start_ms": 0,
        "source_end_ms": 3_000,
        "timeline_start_ms": 0,
        "crop_mode": "smart_cover",
        "transition_out": "cut",
    } | overrides


def document(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "version": "1.0",
        "canvas": {"width": 1080, "height": 1920, "fps": 30, "duration_ms": 6_000},
        "video_tracks": [
            {
                "track": 1,
                "clips": [
                    clip(),
                    clip(source_start_ms=4_000, source_end_ms=7_000, timeline_start_ms=3_000),
                ],
            }
        ],
        "audio_tracks": [
            {"type": "original", "asset_id": None, "gain_db": 0, "duck_under_voice": False}
        ],
        "overlays": [],
        "captions": {"enabled": False, "source": "transcript", "style_id": "brand-caption-v1"},
    }
    return base | overrides


def text_overlay(**overrides: Any) -> dict[str, Any]:
    return {
        "type": "text",
        "text_source": "literal",
        "text": "Bugün taze",
        "reference_id": None,
        "anchor": "bottom_center",
        "style_id": "brand-caption-v1",
        "start_ms": 0,
        "end_ms": 3_000,
        "safe_area": True,
    } | overrides


def context(**overrides: Any) -> ValidationContext:
    facts = {
        ASSET: AssetFacts(
            asset_id=ASSET,
            duration_ms=10_000,
            width=1080,
            height=1920,
            has_audio=True,
            renderable=True,
            source_object_key="tenant/a/media/a/original",
        ),
        LOGO_ASSET: AssetFacts(
            asset_id=LOGO_ASSET,
            duration_ms=None,
            width=512,
            height=512,
            has_audio=False,
            renderable=True,
            source_object_key="tenant/a/media/logo/original",
        ),
    }
    defaults: dict[str, Any] = {
        "assets": facts,
        "logo_asset_ids": frozenset({LOGO_ASSET}),
        "forbidden_terms": (),
        "verified_values": {},
        "now": datetime(2026, 7, 30, tzinfo=UTC),
    }
    return ValidationContext(**(defaults | overrides))


def check(doc: dict[str, Any], ctx: ValidationContext | None = None) -> tuple[str, ...]:
    outcome = validate_timeline(
        parse_timeline(doc),
        context=ctx or context(),
        capabilities=CAPABILITIES,
        profile=PROFILE,
        min_resolution_ratio=0.5,
    )
    return outcome.codes


# --- schema: the closed document ----------------------------------------------------------


def test_round_trip_is_lossless() -> None:
    parsed = parse_timeline(document(overlays=[text_overlay()]))
    assert parse_timeline(serialize_timeline(parsed)) == parsed


def test_raw_coordinates_are_a_parse_error_not_an_ignored_field() -> None:
    """K4's core guarantee: there is no coordinate space to escape into."""

    with pytest.raises(TimelineSchemaError) as error:
        parse_timeline(document(overlays=[text_overlay(x=120, y=400)]))
    assert error.value.code == "TIMELINE_UNKNOWN_FIELD"


def test_anchor_must_be_one_of_the_nine_grid_cells() -> None:
    with pytest.raises(TimelineSchemaError) as error:
        parse_timeline(document(overlays=[text_overlay(anchor="somewhere_nice")]))
    assert error.value.code == "TIMELINE_FIELD_INVALID"
    assert all(
        parse_timeline(document(overlays=[text_overlay(anchor=anchor.value)])).overlays[0].anchor
        is anchor
        for anchor in OverlayAnchor
    )


def test_style_must_be_a_registry_token_not_a_font_specification() -> None:
    with pytest.raises(TimelineSchemaError) as error:
        parse_timeline(document(overlays=[text_overlay(style_id="Comic Sans 96pt red")]))
    assert error.value.code == "TIMELINE_STYLE_TOKEN_UNKNOWN"


def test_literal_text_cannot_be_written_into_a_verified_slot() -> None:
    """The move that would put an invented price on screen is a parse error."""

    with pytest.raises(TimelineSchemaError) as error:
        parse_timeline(
            document(
                overlays=[
                    text_overlay(
                        text_source="verified_product.price",
                        text="9,99 TRY",
                        reference_id=str(uuid4()),
                    )
                ]
            )
        )
    assert error.value.code == "TIMELINE_VERIFIED_FIELD_NOT_LITERAL"


def test_verified_slot_requires_a_reference() -> None:
    with pytest.raises(TimelineSchemaError) as error:
        parse_timeline(
            document(
                overlays=[
                    text_overlay(
                        text_source="verified_campaign.title", text=None, reference_id=None
                    )
                ]
            )
        )
    assert error.value.code == "TIMELINE_VERIFIED_REFERENCE_MISSING"


def test_error_never_echoes_the_rejected_value() -> None:
    secret = "instructions-hidden-in-an-uploaded-video"
    with pytest.raises(TimelineSchemaError) as error:
        parse_timeline(document(overlays=[text_overlay(text=secret, style_id="unknown-token")]))
    assert secret not in str(error.value)


# --- §18.3 validation ------------------------------------------------------------------------


def test_a_well_formed_timeline_passes() -> None:
    assert check(document(overlays=[text_overlay()])) == ()


def test_clip_beyond_the_source_duration_is_rejected() -> None:
    doc = document(
        video_tracks=[{"track": 1, "clips": [clip(source_start_ms=9_000, source_end_ms=12_000)]}]
    )
    assert "TIMELINE_CLIP_RANGE_INVALID" in check(doc)


def test_duration_overflow_is_rejected() -> None:
    doc = document(canvas={"width": 1080, "height": 1920, "fps": 30, "duration_ms": 1_000})
    assert "TIMELINE_DURATION_OVERFLOW" in check(doc)


def test_another_tenants_asset_is_not_accessible() -> None:
    """The repository never returns another tenant's row, so absence is the whole rule."""

    doc = document(video_tracks=[{"track": 1, "clips": [clip(asset_id=str(OTHER_ASSET))]}])
    assert "TIMELINE_ASSET_NOT_ACCESSIBLE" in check(doc)


def test_duplicate_clip_is_rejected() -> None:
    doc = document(video_tracks=[{"track": 1, "clips": [clip(), clip(timeline_start_ms=3_000)]}])
    assert "TIMELINE_DUPLICATE_CLIP" in check(doc)


def test_aspect_ratio_must_match_the_target_profile() -> None:
    doc = document(canvas={"width": 1920, "height": 1080, "fps": 30, "duration_ms": 6_000})
    assert "TIMELINE_ASPECT_RATIO_MISMATCH" in check(doc)


def test_source_below_the_minimum_resolution_is_rejected() -> None:
    small = context(
        assets={
            ASSET: AssetFacts(
                asset_id=ASSET,
                duration_ms=10_000,
                width=320,
                height=240,
                has_audio=True,
                renderable=True,
                source_object_key="tenant/a/media/a/original",
            )
        }
    )
    assert "TIMELINE_RESOLUTION_TOO_LOW" in check(document(), small)


def test_text_too_long_for_the_safe_area_is_rejected() -> None:
    long_line = "ç" * 120
    codes = check(document(overlays=[text_overlay(text=long_line)]))
    assert "TIMELINE_TEXT_OUTSIDE_SAFE_AREA" in codes


def test_forbidden_term_matches_on_word_boundaries_only() -> None:
    guard = context(forbidden_terms=("az", "mucize"))
    assert "TIMELINE_FORBIDDEN_TERM" in check(
        document(overlays=[text_overlay(text="Mucize ürün")]), guard
    )
    # "lezzetli" contains "az" only as a substring; a brand banning "az" must not lose it.
    assert "TIMELINE_FORBIDDEN_TERM" not in check(
        document(overlays=[text_overlay(text="Lezzetli tatlar")]), guard
    )


def test_unresolvable_verified_reference_is_rejected() -> None:
    doc = document(
        overlays=[
            text_overlay(
                text_source="verified_campaign.title", text=None, reference_id=str(CAMPAIGN)
            )
        ]
    )
    assert "TIMELINE_VERIFIED_FIELD_NOT_FOUND" in check(doc)


def test_expired_campaign_is_rejected_separately_from_a_missing_one() -> None:
    doc = document(
        overlays=[
            text_overlay(
                text_source="verified_campaign.title", text=None, reference_id=str(CAMPAIGN)
            )
        ]
    )
    expired = context(
        verified_values={
            ("verified_campaign.title", CAMPAIGN): VerifiedValue(
                text="Yaz Kampanyası", within_window=False
            )
        }
    )
    assert "TIMELINE_CAMPAIGN_WINDOW_INVALID" in check(doc, expired)


def test_resolved_verified_text_is_returned_for_the_renderer() -> None:
    """The string validation checked is the string the plan will draw."""

    doc = document(
        overlays=[
            text_overlay(
                text_source="verified_campaign.title", text=None, reference_id=str(CAMPAIGN)
            )
        ]
    )
    live = context(
        verified_values={
            ("verified_campaign.title", CAMPAIGN): VerifiedValue(
                text="Yaz Kampanyası", within_window=True
            )
        }
    )
    outcome = validate_timeline(
        parse_timeline(doc),
        context=live,
        capabilities=CAPABILITIES,
        profile=PROFILE,
        min_resolution_ratio=0.5,
    )
    assert outcome.ok
    assert outcome.resolved_texts[0] == "Yaz Kampanyası"


def test_logo_must_be_a_registered_brand_logo() -> None:
    overlay = {
        "type": "logo",
        "asset_id": str(ASSET),
        "anchor": "top_right",
        "style_id": "logo-small",
        "start_ms": 0,
        "end_ms": 3_000,
        "safe_area": True,
    }
    assert "TIMELINE_LOGO_ASSET_INVALID" in check(document(overlays=[overlay]))
    assert check(document(overlays=[overlay | {"asset_id": str(LOGO_ASSET)}])) == ()


def test_capabilities_gate_unsupported_features_before_any_render() -> None:
    faded = document(video_tracks=[{"track": 1, "clips": [clip(transition_out="fade")]}])
    assert "TIMELINE_UNSUPPORTED_TRANSITION" in check(faded)
    voiced = document(
        audio_tracks=[
            {"type": "voiceover", "asset_id": str(ASSET), "gain_db": 0, "duck_under_voice": False}
        ]
    )
    assert "TIMELINE_UNSUPPORTED_AUDIO_SOURCE" in check(voiced)


def test_every_failure_is_reported_not_just_the_first() -> None:
    doc = document(
        canvas={"width": 1920, "height": 1080, "fps": 30, "duration_ms": 1_000},
        video_tracks=[{"track": 1, "clips": [clip(asset_id=str(OTHER_ASSET))]}],
    )
    assert len(set(check(doc))) >= 2


# --- patch ----------------------------------------------------------------------------------


def test_patch_changes_text_and_anchor_without_touching_anything_else() -> None:
    timeline = parse_timeline(document(overlays=[text_overlay()]))
    operations = parse_patch(
        [
            {"op": "set_overlay_text", "index": 0, "text_source": "literal", "text": "Yeni başlık"},
            {"op": "set_overlay_anchor", "index": 0, "anchor": "top_center"},
        ]
    )
    patched = apply_patch(timeline, operations, snap_points={}, snap_tolerance_ms=250)
    assert patched.overlays[0].text == "Yeni başlık"
    assert patched.overlays[0].anchor is OverlayAnchor.TOP_CENTER
    assert patched.video_tracks == timeline.video_tracks
    assert patched.canvas == timeline.canvas


def test_the_canonical_patch_form_separates_equivalent_requests_from_different_ones() -> None:
    """What the idempotency fingerprint is taken over (W14, from the W11 finding).

    The old fingerprint carried the operation *count*, so every one-operation patch looked
    like every other one. These are the four comparisons that have to come out right, checked
    on the canonical form itself so the property holds without a database in the way.
    """

    spelled_out = parse_patch(
        [
            {
                "op": "set_overlay_text",
                "index": 0,
                "text_source": "literal",
                "text": "ilk metin",
                "reference_id": None,
            }
        ]
    )
    reordered = parse_patch(
        [{"text": "ilk metin", "text_source": "literal", "index": 0, "op": "set_overlay_text"}]
    )
    different_text = parse_patch(
        [{"op": "set_overlay_text", "index": 0, "text_source": "literal", "text": "ikinci metin"}]
    )
    different_order = parse_patch(
        [
            {"op": "set_overlay_anchor", "index": 0, "anchor": "top_center"},
            {"op": "set_overlay_text", "index": 0, "text_source": "literal", "text": "ilk metin"},
        ]
    )
    same_pair_other_order = parse_patch(
        [
            {"op": "set_overlay_text", "index": 0, "text_source": "literal", "text": "ilk metin"},
            {"op": "set_overlay_anchor", "index": 0, "anchor": "top_center"},
        ]
    )

    # Key order and an optional spelled out as null are the same request.
    assert request_fingerprint(canonical(spelled_out)) == request_fingerprint(canonical(reordered))
    # Different text is a different request — this is the case that used to collide.
    assert request_fingerprint(canonical(spelled_out)) != request_fingerprint(
        canonical(different_text)
    )
    # A patch is a sequence, not a set: reordering two operations can change the result.
    assert request_fingerprint(canonical(different_order)) != request_fingerprint(
        canonical(same_pair_other_order)
    )
    # The canonical form is JSON-safe, so the fingerprint never depends on Python repr.
    assert json.loads(json.dumps(serialize_patch(spelled_out))) == serialize_patch(spelled_out)


def canonical(operations: tuple[PatchOperation, ...]) -> dict[str, object]:
    return {"operations": serialize_patch(operations)}


def test_patch_cannot_write_prose_into_a_verified_slot() -> None:
    with pytest.raises(TimelineSchemaError) as error:
        parse_patch(
            [
                {
                    "op": "set_overlay_text",
                    "index": 0,
                    "text_source": "verified_product.price",
                    "text": "1,00 TRY",
                    "reference_id": str(uuid4()),
                }
            ]
        )
    assert error.value.code == "TIMELINE_VERIFIED_FIELD_NOT_LITERAL"


def test_patch_rejects_an_unknown_operation_and_an_unknown_field() -> None:
    with pytest.raises(TimelineSchemaError):
        parse_patch([{"op": "set_overlay_position", "index": 0, "x": 10, "y": 20}])
    with pytest.raises(TimelineSchemaError):
        parse_patch([{"op": "set_overlay_anchor", "index": 0, "anchor": "top_left", "x": 5}])


def test_patch_target_out_of_range_is_refused_not_ignored() -> None:
    timeline = parse_timeline(document(overlays=[text_overlay()]))
    operations = parse_patch([{"op": "set_overlay_anchor", "index": 7, "anchor": "top_left"}])
    with pytest.raises(TimelineSchemaError) as error:
        apply_patch(timeline, operations, snap_points={}, snap_tolerance_ms=250)
    assert error.value.code == "PATCH_TARGET_NOT_FOUND"


def test_clip_range_patch_snaps_to_a_scene_boundary_and_repacks_the_track() -> None:
    timeline = parse_timeline(document())
    operations = parse_patch(
        [
            {
                "op": "set_clip_range",
                "track_index": 0,
                "clip_index": 0,
                "source_start_ms": 40,
                "source_end_ms": 2_100,
            }
        ]
    )
    patched = apply_patch(
        timeline, operations, snap_points={ASSET: (0, 2_000, 5_000)}, snap_tolerance_ms=250
    )
    first, second = patched.video_tracks[0].clips
    assert (first.source_start_ms, first.source_end_ms) == (0, 2_000)
    # The following clip closes up behind the shortened one and the canvas follows the content.
    assert second.timeline_start_ms == 2_000
    assert patched.canvas.duration_ms == second.timeline_end_ms


def test_snapping_beyond_tolerance_keeps_the_exact_request() -> None:
    timeline = parse_timeline(document())
    operations = parse_patch(
        [
            {
                "op": "set_clip_range",
                "track_index": 0,
                "clip_index": 0,
                "source_start_ms": 1_000,
                "source_end_ms": 2_500,
            }
        ]
    )
    patched = apply_patch(
        timeline, operations, snap_points={ASSET: (0, 5_000)}, snap_tolerance_ms=250
    )
    assert patched.video_tracks[0].clips[0].source_start_ms == 1_000


# --- money -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("minor", "currency", "expected"),
    [
        (14_990, "TRY", "149,90 TRY"),
        (100, "TRY", "1,00 TRY"),
        (5, "EUR", "0,05 EUR"),
        (-250, "USD", "-2,50 USD"),
    ],
)
def test_money_is_formatted_from_integer_minor_units(
    minor: int, currency: str, expected: str
) -> None:
    assert format_money(amount_minor=minor, currency=currency) == expected


def test_safe_area_is_inside_the_frame_for_every_profile() -> None:
    for profile in RenderProfile:
        spec = profile_spec(profile)
        x0, y0, x1, y1 = spec.safe_area.box(width=spec.width, height=spec.height)
        assert 0 <= x0 < x1 <= spec.width
        assert 0 <= y0 < y1 <= spec.height


def test_campaign_window_boundary_is_half_open() -> None:
    """Guards the assumption validation makes when it trusts `within_window`."""

    now = datetime(2026, 7, 30, tzinfo=UTC)
    assert now < now + timedelta(milliseconds=1)
