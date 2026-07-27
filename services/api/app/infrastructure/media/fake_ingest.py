"""Safe byte-free adapters for deterministic ingest tests and development."""

from __future__ import annotations

from pathlib import Path

from app.modules.media.ingest import (
    ContentInspectionResult,
    ContentInspectionUnavailableError,
    MalwareScanPort,
    MalwareScanUnavailableError,
)
from app.modules.media.models import MalwareScanStatus
from app.modules.media.technical import TechnicalPermanentError


class FakeContentInspector:
    def __init__(self) -> None:
        self._results: dict[str, str] = {}
        self._unavailable: set[str] = set()

    async def inspect(self, *, object_key: str, timeout_seconds: int) -> ContentInspectionResult:
        del timeout_seconds
        if object_key in self._unavailable:
            raise ContentInspectionUnavailableError("content inspection unavailable")
        return ContentInspectionResult(self._results.get(object_key, "video/mp4"))

    def set_result_for_testing(self, *, object_key: str, content_type: str) -> None:
        self._results[object_key] = content_type

    def fail_for_testing(self, object_key: str) -> None:
        self._unavailable.add(object_key)


class FakeMalwareScanner(MalwareScanPort):
    def __init__(self) -> None:
        self._results: dict[str, MalwareScanStatus] = {}
        self._unavailable: set[str] = set()

    async def scan(self, *, object_key: str, timeout_seconds: int) -> MalwareScanStatus:
        del timeout_seconds
        if object_key in self._unavailable:
            raise MalwareScanUnavailableError("malware scanner unavailable")
        return self._results.get(object_key, MalwareScanStatus.CLEAN)

    def set_result_for_testing(self, *, object_key: str, status: MalwareScanStatus) -> None:
        self._results[object_key] = status

    def fail_for_testing(self, object_key: str) -> None:
        self._unavailable.add(object_key)


class FakeMediaMaterializer:
    """Fixture-backed worker input adapter; it never exposes storage credentials."""

    def __init__(self) -> None:
        self._fixtures: dict[str, Path] = {}

    def register_for_testing(self, *, object_key: str, fixture_path: Path) -> None:
        self._fixtures[object_key] = fixture_path

    async def materialize(self, *, object_key: str, workdir: Path) -> Path:
        del workdir
        try:
            return self._fixtures[object_key]
        except KeyError as error:
            raise TechnicalPermanentError("MATERIALIZATION_NOT_FOUND") from error
