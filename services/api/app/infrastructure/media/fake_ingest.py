"""Safe byte-free adapters for deterministic ingest tests and development."""

from __future__ import annotations

import shutil
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

    def __init__(self, *, allow_missing_for_testing: bool = False) -> None:
        self._fixtures: dict[str, Path] = {}
        self._allow_missing_for_testing = allow_missing_for_testing

    def register_for_testing(self, *, object_key: str, fixture_path: Path) -> None:
        self._fixtures[object_key] = fixture_path

    async def materialize(self, *, object_key: str, workdir: Path) -> Path:
        source = self._fixtures.get(object_key)
        if source is None:
            if not self._allow_missing_for_testing:
                raise TechnicalPermanentError("MATERIALIZATION_NOT_FOUND")
            workdir.mkdir(parents=True, exist_ok=True)
            destination = workdir / "materialized.mp4"
            destination.write_bytes(b"test-only-media")
            return destination
        if source.is_symlink() or not source.is_file():
            raise TechnicalPermanentError("MATERIALIZATION_SOURCE_INVALID")
        workdir.mkdir(parents=True, exist_ok=True)
        destination = workdir / f"materialized{source.suffix.lower()}"
        shutil.copyfile(source, destination)
        return destination
