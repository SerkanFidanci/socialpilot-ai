"""One normalization step, and one alphabet restriction, for every rule that matches text.

A deterministic detector matches characters, so an attacker only has to change the characters
without changing what a human reads. Codex proved that three times. First against W13's detector:
`1​6​5​TL` (zero-width spaces between the digits), `Türk lirası`
(NFD: `u` plus a combining diaeresis instead of `ü`) and `YİRMİ` (`I` plus a
combining dot instead of `İ`). Then against W16's first fix: `165 ⲦL` with a Coptic capital tau,
and `1⁥6⁥5⁥TL` with U+2065, an *unassigned* code point. Then, from W16's own adversarial round,
`165 ṬL` and `165 ŦL` — Latin letters, so no alphabet rule refused them, wearing a diacritic the
fold did not remove.

Those rounds are the argument for the shape of this module. **Folding** answers "the same letter
written another way"; it cannot answer "a letter from an alphabet nobody thought of", because
that set grows with every Unicode release. So folding is paired with a **restriction**, and W17
makes the two one decision rather than two:

`normalize_for_matching` folds all the way down to ASCII — invisibles out, compatibility forms
in, confusables to ASCII, Turkish case, then every remaining Latin letter onto the ASCII letter
it is built from (`ṭ`→`t`, `ş`→`s`, `ı`→`i`, `Ł`→`l`, `ß`→`ss`). Every literal-text rule matches
on its output, and every pattern literal is therefore written in that same folded alphabet:
`165 turk lirasi`, `165 ṬL` and `165 TL` are one input by the time a rule sees them.

`contains_unsupported_letter` restricts, using **the same fold**: a letter that the fold cannot
put on the ASCII alphabet is refused before any rule runs. That shared implementation is the
point — the set of letters a rule can reason about and the set of letters the parser admits
cannot drift apart, because they are computed by one function. Coptic, Cherokee, Lisu and the
next alphabet someone finds are all unfoldable, and so is a Latin letter whose base this module
cannot name (`ƻ`, `ʭ`). Unfoldable fails closed: at worst a legitimate business name is refused
and this module gains one line, which is the cheap direction of the trade.

`normalize_encoding` is the fold **without** the Latin step, and it exists for values that are
*kept* rather than matched: a scene tag is stored and later compared against video-understanding
labels, so folding `ürün` to `urun` there would silently stop it matching anything. Nothing on
the storage path may call `normalize_for_matching`.

The order of the steps is load-bearing:

1. **Undecorate compatibility digits** (matching path only). NFKC expands `⑴` to `(1)`, which
   inserts punctuation *into a run of digits* and breaks the adjacency every price pattern is
   built on — `⑴⑸ TL` read as a price and matched nothing. A single code point that NFKC turns
   into one decorated number is replaced by the digits alone. This is deliberately narrower than
   letting the patterns skip punctuation between digits: it can only fire on a character that
   was already pretending to be a digit, so ordinary text like `(1) madde (5) fıkra` — written
   with ASCII parentheses — is untouched.
2. **Strip the invisible categories** — `Cf` (format), `Cn` (unassigned), `Co` (private use) and
   `Cs` (surrogate). They are invisible, and one sitting between a base letter and its combining
   mark would otherwise block step 3 from composing them. `Cc` is deliberately absent: a control
   character in provider output is a documented *rejection* in `script.py`, not something to
   quietly clean up.
3. **NFKC.** This composes `u`+`¨` into `ü` and `I`+`◌̇` into `İ`, and folds the compatibility
   forms — fullwidth `１６５`, superscripts, mathematical alphanumerics, NBSP — onto their plain
   equivalents.
4. **Strip what survived**: the invisible categories again (NFKC preserves soft hyphen and BOM),
   the combining-mark categories that did not compose onto anything — `T` plus a combining acute
   has no precomposed form, so without this the detector would never see `TL` — and the few
   assigned code points that render as nothing and that no category names.
5. **Fold confusables**, so a Cyrillic `Т` cannot stand in for a Latin `T`. With the restriction
   in place this is a second layer rather than the defence, and it is kept because it produces
   the *accurate* rejection (an invented price) where the restriction alone would only say the
   alphabet was wrong.
6. **Turkish-aware lowercasing**, on text that is now composed — which is what makes the
   combining-dot `İ` reachable by the existing one-to-one `İ`→`i`, `I`→`ı` map.
7. **The Latin fold, last** (matching path only), on lowercased text.

This module is deliberately free of any content rule. Slice 2D merges the timeline's
`forbidden_matcher` onto the same folding, and it imports these functions rather than growing a
second, subtly different copy.
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from string import ascii_lowercase
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
# merely padding the string. `U+FFA0` folds onto `U+3164` in step 3 and is caught here.
_INVISIBLE_CODE_POINTS: Final = frozenset("ᅟᅠㅤ⠀")

# Alphabets whose letters are drawn with the same glyph as an ASCII letter. Kept as parallel
# strings, one alphabet per line, so the alignment can be checked column by column; a test
# asserts each pair has equal length, that no source character is ASCII, and that every target
# is. Turkish copy contains no Cyrillic or Greek, so folding these cannot cost a real sentence.
# This table is no longer load-bearing — `contains_unsupported_letter` refuses the whole class —
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

# A Latin code point says what it is built from, in its own Unicode name: `LATIN CAPITAL LETTER
# T WITH STROKE`, `LATIN SMALL LETTER G WITH BREVE`, `LATIN SMALL LIGATURE OE`. Reading the base
# out of the name closes the class that a hand-written table only samples — `Ŧ`, `Ⱦ`, `Ƭ`, `Ʈ`,
# `Ț` and every future "T with something" answer to one rule — and it stays correct across
# Unicode releases, because the name ships with the interpreter rather than with this file.
_LETTER_NAME: Final = re.compile(r"LATIN (?:CAPITAL |SMALL )?(?:LETTER|LIGATURE) (.+)")

# The bases the name may end in. Single ASCII letters are generated; the rest are the European
# letters whose names are words rather than letters, and they have to be listed because the name
# cannot be trusted to spell the fold: `THORN` is `th`, not `thorn`, and `SCHWA` is `e`. That is
# also why this map is an allowlist — a base it does not name folds to nothing, and an unfoldable
# letter is refused rather than guessed at.
_NAMED_BASES: Final = {
    **{letter.upper(): letter for letter in ascii_lowercase},
    "AE": "ae",
    "OE": "oe",
    "IJ": "ij",
    "DZ": "dz",
    "LJ": "lj",
    "NJ": "nj",
    "SHARP S": "ss",
    "THORN": "th",
    "ETH": "d",
    "ENG": "n",
    "SCHWA": "e",
    "DOTLESS I": "i",
    "DOTLESS J": "j",
    "LONG S": "s",
    "KRA": "k",
    "HWAIR": "hv",
}

# One code point whose NFKC form is a single number wearing parentheses or a full stop: `⑴` is
# `(1)`, `⒈` is `1.`. Matched against the *expansion of one character*, never against running
# text, which is what keeps ASCII `(1) madde (5) fıkra` out of its reach.
_DECORATED_DIGIT: Final = re.compile(r"\(?(\d+)[.)]?")


def normalize_for_matching(text: str) -> str:
    """Fold `text` into the single form every literal-text rule matches against.

    The result is ASCII letters, digits and punctuation: every Latin letter has been reduced to
    the letter it is built from, so `165 türk lirası`, `165 turk lirasi` and `165 ṬL` arrive at
    the same rule as the same string. Pattern literals are written in this alphabet too — a rule
    spelled `yüzde` would never fire again.

    The result is for *matching only*. It is never stored and never shown: it deliberately
    destroys information (invisible characters, stray combining marks, letter case, and now
    every diacritic Turkish spelling depends on) that the original text is entitled to keep.
    Use `normalize_encoding` for a value that survives the call.
    """

    return _fold_latin(normalize_encoding(_undecorate_digits(text)))


def normalize_encoding(text: str) -> str:
    """Fold away *how* `text` is encoded, leaving which letters it is written in alone.

    This is the half of the fold that is safe on a value that gets kept: a scene tag is stored
    and later compared against labels produced by video understanding, so `ürün` has to stay
    `ürün` and not become `urun`. Re-encodings still collapse — invisible characters, NFD
    spellings, fullwidth forms, a Cyrillic lookalike, letter case.
    """

    return _compose(text).translate(_CONFUSABLE_FOLD).translate(_TURKISH_FOLD).lower()


def contains_unsupported_letter(text: str) -> bool:
    """True when `text` carries a letter the fold cannot put on the ASCII alphabet.

    Folding is an allowlist of known lookalikes, which is the same shape of defence that lost to
    `Ⲧ`. This is the complement, and it is deliberately expressed through `_ascii_fold`: a letter
    is admissible exactly when the rules can read it. Two separate lists — one of letters allowed
    in, one of letters folded — would eventually disagree, and `ṬL` is what that disagreement
    looks like from the outside.

    Non-letters are untouched — digits from other numbering systems are already a price rule's
    business, and emoji and punctuation are nobody's.

    The check runs on composed text but *before* confusable folding, or a Cyrillic `а` would
    have already become an `a` and answered for itself.
    """

    prepared = _compose(text)
    if prepared.isascii():
        return False
    return any(
        unicodedata.category(character).startswith("L") and _ascii_fold(character) is None
        for character in prepared
    )


@lru_cache(maxsize=4096)
def _ascii_fold(character: str) -> str | None:
    """The ASCII spelling of one character, or `None` when this module cannot name one.

    Decomposition first, because it is exact and covers everything Turkish and most of Europe:
    `ş` is `s` plus a cedilla, `ṭ` is `t` plus a dot below. The Unicode name is the fallback for
    the letters that have no decomposition at all — `Ŧ`, `Đ`, `Ł`, `Ø`, `ı` are single code
    points, which is precisely why enumerating them by hand would have been endless.
    """

    if character.isascii():
        return character.lower()
    decomposed = "".join(
        part
        for part in unicodedata.normalize("NFD", character)
        if unicodedata.category(part) not in _MARK_CATEGORIES
    )
    if decomposed.isascii() and decomposed.isalpha():
        return decomposed.lower()
    name = _LETTER_NAME.fullmatch(unicodedata.name(character, ""))
    if name is None:
        return None
    return _NAMED_BASES.get(name.group(1).split(" WITH ")[0])


def _fold_latin(text: str) -> str:
    """Step 7. A character with no ASCII spelling is left as it is rather than dropped.

    Dropping it would join its neighbours into a word neither the author nor the reader wrote,
    and this function has no standing to refuse anything: `parse_text` already refused the ones
    that matter, and the rest — `₺`, `—`, emoji — are not letters and belong in the patterns.
    """

    if text.isascii():
        return text
    return "".join(_ascii_fold(character) or character for character in text)


def _undecorate_digits(text: str) -> str:
    """Step 1. Replace a one-character decorated number with the number alone."""

    if text.isascii():
        return text
    return "".join(_undecorated(character) for character in text)


@lru_cache(maxsize=4096)
def _undecorated(character: str) -> str:
    if character.isascii():
        return character
    expanded = _DECORATED_DIGIT.fullmatch(unicodedata.normalize("NFKC", character))
    if expanded is not None:
        return expanded.group(1)
    try:
        # Unicode says this character *is* a digit even though NFKC leaves it alone and `\d`
        # does not match it: `⓵` and `❶` are category `No`. W16 left non-letters to the price
        # rule on the grounds that another numbering system's digit is already its business —
        # true for `١٦٥`, which `\d` matches, and false for these, which it does not. Asking
        # Unicode for the value closes that gap for every such character at once.
        return str(unicodedata.digit(character))
    except ValueError:
        return character


def _compose(text: str) -> str:
    """Steps 2–4: drop the invisibles, compose, drop what could not compose."""

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
