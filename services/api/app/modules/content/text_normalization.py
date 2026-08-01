"""One normalization step, and one alphabet restriction, for every rule that matches text.

A deterministic detector matches characters, so an attacker only has to change the characters
without changing what a human reads. Codex proved that twice. First against W13's detector:
`1​6​5​TL` (zero-width spaces between the digits), `Türk lirası`
(NFD: `u` plus a combining diaeresis instead of `ü`) and `YİRMİ` (`I` plus a
combining dot instead of `İ`). Then against W16's first fix: `165 ⲦL` with a Coptic capital tau,
and `1⁥6⁥5⁥TL` with U+2065, an *unassigned* code point.

Those two rounds are the argument for the shape of this module. **Folding** answers "the same
letter written another way"; it cannot answer "a letter from an alphabet nobody thought of",
because that set grows with every Unicode release. So folding is paired with a **restriction**:

`normalize_for_matching` folds — invisibles out, compatibility forms in, confusables to ASCII,
Turkish case last. Every literal-text rule matches on its output.

`contains_non_latin_letter` restricts — literal prose in a script document is written in Latin
script, and anything else is refused before a rule ever runs. This is the part that closes the
class rather than the example: Coptic, Cherokee, Lisu, Deseret and the next alphabet someone
finds are all "not Latin", and no table has to grow to say so.

The order of the folding steps is load-bearing:

1. **Strip the invisible categories first** — `Cf` (format), `Cn` (unassigned), `Co` (private
   use) and `Cs` (surrogate). They are invisible, and one sitting between a base letter and its
   combining mark would otherwise block step 2 from composing them. `Cc` is deliberately absent:
   a control character in provider output is a documented *rejection* in `script.py`, not
   something to quietly clean up.
2. **NFKC.** This composes `u`+`¨` into `ü` and `I`+`◌̇` into `İ`, and folds the compatibility
   forms — fullwidth `１６５`, superscripts, mathematical alphanumerics, NBSP — onto their plain
   equivalents.
3. **Strip what survived**: the invisible categories again (NFKC preserves soft hyphen and BOM),
   the combining-mark categories that did not compose onto anything — `T` plus a combining acute
   has no precomposed form, so without this the detector would never see `TL` — and the few
   assigned code points that render as nothing and that no category names.
4. **Fold confusables**, so a Cyrillic `Т` cannot stand in for a Latin `T`. With the restriction
   in place this is a second layer rather than the defence, and it is kept because it produces
   the *accurate* rejection (an invented price) where the restriction alone would only say the
   alphabet was wrong.
5. **Turkish-aware lowercasing last**, on text that is now composed — which is what makes the
   combining-dot `İ` reachable by the existing one-to-one `İ`→`i`, `I`→`ı` map.

This module is deliberately free of any content rule. Slice 2D merges the timeline's
`forbidden_matcher` onto the same folding, and it imports these functions rather than growing a
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

# Categories, not code points. An enumeration is how U+2065 got through the first fix: it is
# unassigned, so it appeared on no list of invisible characters, and Unicode has ~800k more
# unassigned code points where that came from. `Cn` covers all of them at once, and covers them
# in the Unicode version this interpreter ships rather than the one the list was written against.
_IGNORED_CATEGORIES: Final = frozenset({"Cf", "Cn", "Co", "Cs"})
_MARK_CATEGORIES: Final = frozenset({"Mn", "Me", "Mc"})
_STRIPPED_AFTER_COMPOSING: Final = _IGNORED_CATEGORIES | _MARK_CATEGORIES

# Assigned, named, and still renders as nothing — so no category rule reaches them. The Hangul
# fillers are the reason this set exists at all: they are category `Lo`, *word characters*, so
# one next to a figure defeats the `(?<!\w)` boundary the price patterns are built on rather than
# merely padding the string. `U+FFA0` folds onto `U+3164` in step 2 and is caught here.
_INVISIBLE_CODE_POINTS: Final = frozenset("ᅟᅠㅤ⠀")

# Alphabets whose letters are drawn with the same glyph as an ASCII letter. Kept as parallel
# strings, one alphabet per line, so the alignment can be checked column by column; a test
# asserts each pair has equal length, that no source character is ASCII, and that every target
# is. Turkish copy contains no Cyrillic or Greek, so folding these cannot cost a real sentence.
# This table is no longer load-bearing — `contains_non_latin_letter` refuses the whole class —
# but it keeps the *reason* for a rejection accurate on the alphabets that actually occur.
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

# Every Latin code point's name starts this way — `LATIN SMALL LETTER DOTLESS I`, `LATIN CAPITAL
# LETTER I WITH DOT ABOVE`, `LATIN SMALL LETTER G WITH BREVE`. Turkish is written in exactly
# these, so the whole alphabet the product needs is on the near side of one string comparison.
_LATIN_NAME_PREFIX: Final = "LATIN "


def normalize_for_matching(text: str) -> str:
    """Fold `text` into the single form every literal-text rule matches against.

    The result is for *matching only*. It is never stored and never shown: it deliberately
    destroys information (invisible characters, stray combining marks, letter case) that the
    original text is entitled to keep.
    """

    return _compose(text).translate(_CONFUSABLE_FOLD).translate(_TURKISH_FOLD).lower()


def contains_non_latin_letter(text: str) -> bool:
    """True when `text` carries a letter from an alphabet this product does not write in.

    Folding is an allowlist of known lookalikes, which is the same shape of defence that lost to
    `Ⲧ`. This is the complement: the alphabet a script may be *written in* is bounded, so there
    is no next character to find. Non-letters are untouched — digits from other numbering
    systems are already a price rule's business, and emoji and punctuation are nobody's.

    The check runs on composed text but *before* confusable folding, or a Cyrillic `а` would
    have already become an `a` and answered for itself.
    """

    prepared = _compose(text)
    if prepared.isascii():
        return False
    return any(
        unicodedata.category(character).startswith("L") and not _is_latin(character)
        for character in prepared
    )


def _is_latin(character: str) -> bool:
    if character.isascii():
        return True
    return unicodedata.name(character, "").startswith(_LATIN_NAME_PREFIX)


def _compose(text: str) -> str:
    """Steps 1–3: drop the invisibles, compose, drop what could not compose."""

    return _strip(unicodedata.normalize("NFKC", _strip(text)), _STRIPPED_AFTER_COMPOSING)


def _strip(text: str, categories: frozenset[str] = _IGNORED_CATEGORIES) -> str:
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
