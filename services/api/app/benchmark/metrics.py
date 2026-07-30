"""Pure metric functions comparing a provider output to machine-readable ground truth.

Every function here is deterministic and free of I/O, so a metric value is reproducible for a
given (input, output) pair. Text is compared case-folded with ``str.lower``; this is a known
simplification for Turkish casing (``I``/``ı``) and is documented rather than hidden.
"""

from __future__ import annotations

import re
import statistics
from collections.abc import Sequence

_PRICE_PATTERN = re.compile(
    r"%\s?\d+(?:[.,]\d+)?|₺\s?\d+(?:[.,]\d+)?|\b\d+(?:[.,]\d+)?\s?(?:tl|lira|₺)\b",
    re.IGNORECASE,
)
_TR_MONTHS = (
    "ocak|şubat|subat|mart|nisan|mayıs|mayis|haziran|temmuz|"
    "ağustos|agustos|eylül|eylul|ekim|kasım|kasim|aralık|aralik"
)
_DATE_PATTERN = re.compile(
    rf"\b\d{{1,2}}\s?(?:{_TR_MONTHS})\b|\b\d{{1,2}}[./]\d{{1,2}}(?:[./]\d{{2,4}})?\b",
    re.IGNORECASE,
)


def _tokens(text: str) -> list[str]:
    return text.lower().split()


def word_error_rate(reference: str, hypothesis: str) -> float:
    """Levenshtein word-edit distance divided by the reference word count."""

    ref = _tokens(reference)
    hyp = _tokens(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    previous = list(range(len(hyp) + 1))
    for i, ref_word in enumerate(ref, start=1):
        current = [i]
        for j, hyp_word in enumerate(hyp, start=1):
            substitution = previous[j - 1] + (0 if ref_word == hyp_word else 1)
            current.append(min(previous[j] + 1, current[j - 1] + 1, substitution))
        previous = current
    return previous[-1] / len(ref)


def timestamp_drift_ms(
    reference: Sequence[tuple[int, int]], hypothesis: Sequence[tuple[int, int]]
) -> float:
    """Mean of ``(|Δstart| + |Δend|) / 2`` over index-aligned segments."""

    pairs = min(len(reference), len(hypothesis))
    if pairs == 0:
        return 0.0
    total = 0.0
    for (ref_start, ref_end), (hyp_start, hyp_end) in zip(
        reference[:pairs], hypothesis[:pairs], strict=True
    ):
        total += (abs(ref_start - hyp_start) + abs(ref_end - hyp_end)) / 2
    return total / pairs


def brand_term_hit_rate(hypothesis_text: str, brand_terms: Sequence[str]) -> float:
    """Fraction of expected brand terms present as a substring (case-folded)."""

    if not brand_terms:
        return 1.0
    haystack = hypothesis_text.lower()
    hits = sum(1 for term in brand_terms if term.lower() in haystack)
    return hits / len(brand_terms)


def label_jaccard(predicted: Sequence[str], expected: Sequence[str]) -> float:
    predicted_set = {value.lower() for value in predicted}
    expected_set = {value.lower() for value in expected}
    if not predicted_set and not expected_set:
        return 1.0
    union = predicted_set | expected_set
    if not union:
        return 1.0
    return len(predicted_set & expected_set) / len(union)


def detection_f1(predicted: Sequence[str], expected: Sequence[str]) -> float:
    predicted_set = {value.lower() for value in predicted}
    expected_set = {value.lower() for value in expected}
    if not predicted_set and not expected_set:
        return 1.0
    true_positive = len(predicted_set & expected_set)
    if true_positive == 0:
        return 0.0
    precision = true_positive / len(predicted_set)
    recall = true_positive / len(expected_set)
    return 2 * precision * recall / (precision + recall)


def flags_exact_match(predicted: Sequence[str], expected: Sequence[str]) -> float:
    return 1.0 if {value.lower() for value in predicted} == {v.lower() for v in expected} else 0.0


def count_forbidden_words(text: str, forbidden: Sequence[str]) -> int:
    haystack = text.lower()
    return sum(
        1
        for word in forbidden
        if re.search(rf"\b{re.escape(word.lower())}\b", haystack) is not None
    )


def _normalize_fact(token: str) -> str:
    return re.sub(r"\s+", "", token.lower())


def count_fabricated_facts(
    text: str, allowed_prices: Sequence[str], allowed_dates: Sequence[str]
) -> int:
    """Count price/date tokens in ``text`` that are not in the approved fact list.

    AI output must never invent prices or dates (AGENTS.md); this must be zero for a compliant
    provider.
    """

    allowed = {_normalize_fact(value) for value in (*allowed_prices, *allowed_dates)}
    found = _PRICE_PATTERN.findall(text) + _DATE_PATTERN.findall(text)
    return sum(1 for token in found if _normalize_fact(token) not in allowed)


def cta_from_approved(cta: str, approved: Sequence[str]) -> bool:
    normalized = cta.strip().lower()
    return any(normalized == candidate.strip().lower() for candidate in approved)


def timeline_conforms(timeline: object, duration_ms: int) -> bool:
    """Strict structural check: consecutive, in-bounds, non-overlapping, non-empty segments."""

    if not isinstance(timeline, dict):
        return False
    segments = timeline.get("segments")
    if not isinstance(segments, list) or not segments:
        return False
    previous_end = 0
    for index, segment in enumerate(segments):
        if not isinstance(segment, dict):
            return False
        start = segment.get("start_ms")
        end = segment.get("end_ms")
        text = segment.get("text")
        segment_index = segment.get("index")
        if (
            segment_index != index
            or not isinstance(start, int)
            or not isinstance(end, int)
            or not isinstance(text, str)
            or not text.strip()
            or start < previous_end
            or end <= start
            or end > duration_ms
        ):
            return False
        previous_end = end
    return True


def duration_deviation_ms(reference_ms: Sequence[int], estimated_ms: Sequence[int]) -> float:
    pairs = min(len(reference_ms), len(estimated_ms))
    if pairs == 0:
        return 0.0
    return sum(abs(reference_ms[i] - estimated_ms[i]) for i in range(pairs)) / pairs


def turkish_phoneme_coverage(text: str, phonemes: Sequence[str]) -> float:
    """Fraction of the expected Turkish-specific graphemes actually present in the text."""

    if not phonemes:
        return 1.0
    haystack = text.lower()
    present = sum(1 for phoneme in phonemes if phoneme.lower() in haystack)
    return present / len(phonemes)


def distribution(
    values: Sequence[float],
) -> tuple[float | None, float | None, float | None, float | None]:
    """Return (mean, min, max, stdev) for a run distribution, or Nones when empty."""

    if not values:
        return (None, None, None, None)
    mean_value = statistics.fmean(values)
    stdev_value = statistics.stdev(values) if len(values) > 1 else 0.0
    return (mean_value, min(values), max(values), stdev_value)
