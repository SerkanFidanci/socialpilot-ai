"""One normalization step shared by every rule that matches *literal* text.

A deterministic detector matches characters, so an attacker only has to change the characters
without changing what a human reads. Codex proved that against W13's fabrication detector:
`1​6​5​TL` (zero-width spaces between the digits), `Türk lirası`
(NFD: `u` plus a combining diaeresis instead of `ü`) and `YİRMİ` (`I` plus a
combining dot instead of `İ`) all read exactly like the strings the detector rejects, and all
three walked past it into a stored, human-approvable script.

The fix belongs *before* matching rather than inside each pattern: every rule folds its input
through `normalize_for_matching` first, so a pattern can stay plain lowercase Turkish and still
hold against a hostile encoding of the same sentence. The order of the steps is the load-bearing
part:

1. **Strip `Cf` format characters first.** They are invisible, and one sitting between a base
   letter and its combining mark would otherwise block step 2 from composing them.
2. **NFKC.** This composes `u`+`¨` into `ü` and `I`+`◌̇` into `İ`, and folds the compatibility
   forms — fullwidth `１６５`, superscripts, mathematical alphanumerics, NBSP — onto their plain
   equivalents.
3. **Strip what survived**: `Cf` again (NFKC preserves soft hyphen and BOM), the combining-mark
   categories that did not compose onto anything, and the handful of invisible code points that
   are not classified `Cf` at all. The Hangul fillers are the reason that last set exists: they
   are *letters* to `\\w`, so one next to a figure defeats the `(?<!\\w)` boundary the price
   patterns are built on.
4. **Fold confusables**, so a Cyrillic `Т` cannot stand in for a Latin `T`.
5. **Turkish-aware lowercasing last**, on text that is now composed — which is what makes the
   combining-dot `İ` reachable by the existing one-to-one `İ`→`i`, `I`→`ı` map.

This module is deliberately free of any content rule. Slice 2D merges the timeline's
`forbidden_matcher` onto the same folding, and it imports this function rather than growing a
second, subtly different copy.
"""

from __future__ import annotations

import unicodedata
from typing import Final

# `re.IGNORECASE` does not relate `İ`/`I` to `i`/`ı`: Python lowercases `İ` to two code points,
# so a pattern written with `i` misses a term spelled with `İ`. Folding with a one-to-one
# character map first keeps match offsets meaningful and lets every pattern stay plain lowercase
# with no case flag to forget.
_TURKISH_FOLD: Final = str.maketrans({"İ": "i", "I": "ı"})

# Invisible characters that carry no meaning for matching. `Cf` is the format category (ZWSP
# U+200B, ZWNJ U+200C, ZWJ U+200D, word joiner U+2060, BOM U+FEFF, the bidi marks U+200E/U+200F,
# soft hyphen U+00AD). The mark categories cover a combining sequence that NFKC could not compose
# onto a base letter — `T` plus a combining acute has no precomposed form, so without this the
# detector would never see `TL`.
_FORMAT_CATEGORIES: Final = frozenset({"Cf"})
_INVISIBLE_CATEGORIES: Final = frozenset({"Cf", "Mn", "Me", "Mc"})

# Renders as nothing, but is not classified `Cf`, so category stripping alone leaves it in place.
# The Hangul fillers are category `Lo` — *word characters* — which is what makes them dangerous
# rather than merely untidy. `U+FFA0` folds onto `U+3164` in step 2 and is caught here.
_INVISIBLE_CODE_POINTS: Final = frozenset("ᅟᅠㅤ⠀")

# Alphabets whose letters are drawn with the same glyph as an ASCII letter. Kept as parallel
# strings, one alphabet per line, so the alignment can be checked column by column; a test
# asserts each pair has equal length, that no source character is ASCII, and that every target
# is. Turkish copy contains no Cyrillic or Greek, so folding these cannot cost a real sentence —
# whereas leaving them in hands a hostile provider a spelling of `TL` the detector cannot read.
_CONFUSABLE_PAIRS: Final = (
    ("АВЕКМНОРСТУХЅІЈԚԜ", "ABEKMHOPCTYXSIJQW"),  # Cyrillic capitals
    ("аеорсухѕіјԛԝ", "aeopcyxsijqw"),  # Cyrillic lowercase
    ("ΑΒΕΖΗΙΚΜΝΟΡΤΥΧ", "ABEZHIKMNOPTYX"),  # Greek capitals
    ("αεικνορτυ", "aeikvoptu"),  # Greek lowercase
)

_CONFUSABLE_FOLD: Final = str.maketrans(
    "".join(source for source, _ in _CONFUSABLE_PAIRS),
    "".join(target for _, target in _CONFUSABLE_PAIRS),
)


def normalize_for_matching(text: str) -> str:
    """Fold `text` into the single form every literal-text rule matches against.

    The result is for *matching only*. It is never stored and never shown: it deliberately
    destroys information (invisible characters, stray combining marks, letter case) that the
    original text is entitled to keep.
    """

    stripped = _strip(text, _FORMAT_CATEGORIES)
    composed = unicodedata.normalize("NFKC", stripped)
    cleaned = _strip(composed, _INVISIBLE_CATEGORIES)
    return cleaned.translate(_CONFUSABLE_FOLD).translate(_TURKISH_FOLD).lower()


def _strip(text: str, categories: frozenset[str]) -> str:
    """Drop every character in `categories`, plus the invisibles no category names.

    ASCII text is returned untouched: it can hold no format character, no combining mark and no
    confusable, so the per-character walk would only cost time.
    """

    if text.isascii():
        return text
    return "".join(
        character
        for character in text
        if unicodedata.category(character) not in categories
        and character not in _INVISIBLE_CODE_POINTS
    )
