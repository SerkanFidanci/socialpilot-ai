"""The second gate: every bypass closed on the script side must be closed on the timeline too.

Four adversarial rounds hardened the script detector — invisible characters between digits, a
Coptic capital tau standing in for `T`, an unassigned code point, `ṬL`, `lirayla`. The timeline's
own forbidden-term rule was never part of that work and ran a plain `re.IGNORECASE` matcher over
raw text, which meant every one of those bypasses was still open on the other side of the same
product: a claim the parser refuses in a script could be typed into an overlay and drawn on the
frame.

This file is that gap, closed and pinned. It is deliberately written from the attacker's side —
each test spells a banned term the way someone trying to get it past a checker would — and it
also pins the two things that must **not** change: no stemming, and one implementation rather
than two.
"""

from __future__ import annotations

import ast
import tokenize
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from app.infrastructure.render.fake import FakeRenderAdapter
from app.modules.content import script as script_module
from app.modules.content.render import RenderProfile
from app.modules.content.timeline import parse_timeline
from app.modules.content.validation import (
    AssetFacts,
    ValidationContext,
    validate_timeline,
)

ASSET = UUID("11111111-1111-4111-8111-111111111111")
PROFILE = RenderProfile.INSTAGRAM_REELS_1080X1920
CAPABILITIES = FakeRenderAdapter().capabilities
VALIDATION = Path(__file__).resolve().parents[2] / "app" / "modules" / "content" / "validation.py"


def document(text: str) -> dict[str, Any]:
    return {
        "version": "1.0",
        "canvas": {"width": 1080, "height": 1920, "fps": 30, "duration_ms": 6_000},
        "video_tracks": [
            {
                "track": 1,
                "clips": [
                    {
                        "asset_id": str(ASSET),
                        "source_start_ms": 0,
                        "source_end_ms": 3_000,
                        "timeline_start_ms": 0,
                        "crop_mode": "smart_cover",
                        "transition_out": "cut",
                    }
                ],
            }
        ],
        "audio_tracks": [
            {"type": "original", "asset_id": None, "gain_db": 0, "duck_under_voice": False}
        ],
        "overlays": [
            {
                "type": "text",
                "text_source": "literal",
                "text": text,
                "reference_id": None,
                "anchor": "bottom_center",
                "style_id": "brand-caption-v1",
                "start_ms": 0,
                "end_ms": 3_000,
                "safe_area": True,
            }
        ],
        "captions": {"enabled": False, "source": "transcript", "style_id": "brand-caption-v1"},
    }


def codes(text: str, *, forbidden: tuple[str, ...] = ("şeker",)) -> tuple[str, ...]:
    context = ValidationContext(
        assets={
            ASSET: AssetFacts(
                asset_id=ASSET,
                duration_ms=10_000,
                width=1080,
                height=1920,
                has_audio=True,
                renderable=True,
                source_object_key="tenant/a/media/a/original",
            )
        },
        logo_asset_ids=frozenset(),
        forbidden_terms=forbidden,
        verified_values={},
        now=datetime(2026, 8, 2, tzinfo=UTC),
    )
    outcome = validate_timeline(
        parse_timeline(document(text)),
        context=context,
        capabilities=CAPABILITIES,
        profile=PROFILE,
        min_resolution_ratio=0.5,
    )
    return outcome.codes


# --- the bypasses the script side closed, closed here too ----------------------------------------


def test_the_plain_term_is_still_caught() -> None:
    assert "TIMELINE_FORBIDDEN_TERM" in codes("bol şeker var")


@pytest.mark.parametrize(
    ("spelling", "why"),
    [
        ("bol şe​ker var", "a zero-width space inside the term"),
        ("bol şe⁥ker var", "an unassigned code point inside the term"),
        ("bol şeker var", "NFD: the cedilla as a separate combining mark"),
        ("bol ŞEKER var", "upper case, including the Turkish dotted pair"),
        ("bol seker var", "the accent simply dropped, as a phone keyboard produces"),
        ("bol şekeṛ var", "an extra diacritic the fold removes"),
        ("bol şeker var", "the term itself"),
    ],
)
def test_a_re_spelled_term_does_not_escape_the_frame(spelling: str, why: str) -> None:
    """Each of these was, or is the timeline twin of, a bypass a verification round found."""

    assert "TIMELINE_FORBIDDEN_TERM" in codes(spelling), why


def test_a_letter_the_fold_cannot_spell_is_refused_before_any_rule_runs() -> None:
    """Folding answers "the same letter, written differently"; it cannot answer "a new alphabet".

    `parse_text` refuses these in a script. Refusing them here too is the same fail-closed
    boundary: text nothing can read must not be text something draws.
    """

    result = codes("bol ⲦL var")  # Coptic capital tau standing in for a Latin T
    assert "TIMELINE_UNSUPPORTED_CHARACTER" in result


def test_turkish_text_is_not_collateral_damage() -> None:
    """The alphabet restriction must not refuse the language the product is written in."""

    assert codes("Bugün ığşçöüİĞŞÇÖÜ taze — %20 lezzet") == ()


# --- the two pins that must not drift ------------------------------------------------------------


def test_a_forbidden_term_does_not_ban_the_words_built_on_it() -> None:
    """PM, W18: the list is the brand's and the pattern is ours. `şeker` is not `şekerli`."""

    assert codes("bol şekerli tatlı") == ()


def test_the_existing_word_boundary_pin_survives_the_merge() -> None:
    """`az` forbidden must still leave `lezzetli` alone — the pin W11 set, unchanged."""

    assert codes("çok lezzetli", forbidden=("az",)) == ()
    assert "TIMELINE_FORBIDDEN_TERM" in codes("az tuzlu", forbidden=("az",))


def test_the_timeline_uses_the_script_matcher_rather_than_a_second_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replacing the script's matcher changes timeline validation — proof of one implementation.

    Stronger than reading the import statement: a copied matcher would keep working here.
    """

    monkeypatch.setattr(script_module, "forbidden_matcher", lambda terms: None)
    assert codes("bol şeker var") == ()


def test_validation_holds_no_second_folding_implementation() -> None:
    """The old matcher folded case with `re.IGNORECASE` over unnormalized text. It is gone.

    A second fold is how the two gates drift apart again, and `ṬL` is what that drift looked
    like from the outside — refused in one place, drawn in the other.
    """

    # Tokenized, so prose *about* the old matcher stays allowed and executable code naming it
    # does not — the same distinction `test_render_port.py` draws about provider names.
    executable: list[str] = []
    with VALIDATION.open("rb") as handle:
        for token in tokenize.tokenize(handle.readline):
            if token.type not in (tokenize.COMMENT, tokenize.STRING):
                executable.append(token.string)
    assert "IGNORECASE" not in " ".join(executable)

    tree = ast.parse(VALIDATION.read_text(encoding="utf-8"))
    imported = {
        f"{node.module}.{alias.name}"
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert "app.modules.content.script.forbidden_matcher" in imported
    assert "app.modules.content.text_normalization.normalize_for_matching" in imported
    assert "app.modules.content.text_normalization.contains_unsupported_letter" in imported
