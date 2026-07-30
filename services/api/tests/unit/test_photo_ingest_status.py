"""The photo-analysis ingest status exists but is unreachable (W10, item 3 / K6).

``IngestStatus.READY_FOR_PHOTO_ANALYSIS`` is a seam for the future HEIC/HEIF photo pipeline. It
was added on migration 0011 so the photo slice needs no further enum migration. Until that slice
is built, HEIC/HEIF is still rejected at the ingest gate and nothing may produce this value. The
only reason to add an unreachable status now is to spend the migration slot once; these tests
guard that it stays unreachable.
"""

from __future__ import annotations

from pathlib import Path

from app.modules.media.models import IngestStatus

_MEMBER = "READY_FOR_PHOTO_ANALYSIS"
_VALUE = "ready_for_photo_analysis"
_APP_ROOT = Path(__file__).resolve().parents[2] / "app"
_DEFINING_MODULE = _APP_ROOT / "modules" / "media" / "models.py"


def test_photo_ingest_status_member_exists() -> None:
    assert IngestStatus.READY_FOR_PHOTO_ANALYSIS.value == _VALUE


def test_photo_ingest_status_is_unreachable() -> None:
    """No application module references the value except the enum that defines it."""

    referencing = {
        path
        for path in _APP_ROOT.rglob("*.py")
        if _MEMBER in path.read_text(encoding="utf-8") or _VALUE in path.read_text(encoding="utf-8")
    }

    assert referencing == {_DEFINING_MODULE}, (
        "ready_for_photo_analysis must stay unreachable until the photo pipeline is built; "
        f"unexpected references: {sorted(str(path) for path in referencing - {_DEFINING_MODULE})}"
    )
