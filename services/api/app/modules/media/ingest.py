"""Tenant-safe media ingest application service and provider-neutral ports."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ProblemException
from app.modules.media.models import (
    IngestStatus,
    MalwareScanStatus,
    MediaAsset,
    MediaAssetStatus,
    MediaIngestInspection,
    MediaMalwareScan,
)
from app.modules.media.repository import MediaRepository
from app.modules.media.storage import (
    MultipartStoragePort,
    StoragePermanentError,
    StorageUnavailableError,
    StoredObjectMetadata,
)
from app.modules.operations.models import BackgroundJob, JobAttempt, JobAttemptStatus, JobStatus
from app.modules.operations.repository import OperationsRepository
from app.modules.operations.service import OperationsService


@dataclass(frozen=True)
class ContentInspectionResult:
    detected_content_type: str


class ContentInspectionPort(Protocol):
    async def inspect(self, *, object_key: str, timeout_seconds: int) -> ContentInspectionResult:
        """Inspect bytes at storage through a worker-side adapter, never through FastAPI."""


class MalwareScanPort(Protocol):
    async def scan(self, *, object_key: str, timeout_seconds: int) -> MalwareScanStatus:
        """Return a neutral scan verdict without provider diagnostics."""


class ContentInspectionUnavailableError(RuntimeError):
    """Transient inspection dependency failure."""


class MalwareScanUnavailableError(RuntimeError):
    """Transient malware-scanner dependency failure."""


class IngestValidationError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        quarantine: bool = False,
        scan_status: MalwareScanStatus | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.quarantine = quarantine
        self.scan_status = scan_status


class MediaIngestService:
    """Claim and execute durable ingest jobs without accepting media bytes."""

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        storage: MultipartStoragePort,
        content_inspector: ContentInspectionPort,
        malware_scanner: MalwareScanPort,
    ) -> None:
        self._session = session
        self._settings = settings
        self._storage = storage
        self._content_inspector = content_inspector
        self._malware_scanner = malware_scanner
        self._media = MediaRepository(session)
        self._operations = OperationsRepository(session)

    async def claim_next(self) -> BackgroundJob | None:
        """Atomically claim one due ingest job using SKIP LOCKED."""

        async with self._session.begin():
            job = await self._operations.claim_next_ingest_job()
            if job is None:
                return None
            now = datetime.now(UTC)
            job.status = JobStatus.RUNNING
            job.attempt_count += 1
            job.started_at = now
            job.finished_at = None
            job.next_attempt_at = None
            job.last_error_code = None
            job.last_error_summary = None
            self._operations.add(
                JobAttempt(
                    job_id=job.id,
                    attempt_number=job.attempt_count,
                    status=JobAttemptStatus.STARTED,
                    correlation_id=job.correlation_id,
                )
            )
            return job

    async def process_next(self) -> BackgroundJob | None:
        job = await self.claim_next()
        if job is None:
            return None
        return await self.process_claimed(business_id=job.business_id, job_id=job.id)

    async def process_claimed(self, *, business_id: UUID, job_id: UUID) -> BackgroundJob:
        """Execute a previously claimed job; foreign-tenant access is non-disclosing."""

        async with self._session.begin():
            job, asset = await self._running_job_and_asset(business_id, job_id)
            asset.ingest_status = IngestStatus.VALIDATING
            object_key = asset.storage_object_key
            expected = StoredObjectMetadata(
                byte_size=asset.byte_size,
                content_type=asset.content_type,
                sha256_checksum=asset.sha256_checksum,
                etag="",
            )
        try:
            metadata = await self._get_metadata(object_key)
            self._validate_metadata(expected, metadata)
            inspection = await self._content_inspector.inspect(
                object_key=object_key, timeout_seconds=self._settings.media_ingest_timeout_seconds
            )
            if (
                inspection.detected_content_type.lower()
                not in self._settings.media_allowed_mime_types
            ):
                raise IngestValidationError("INGEST_CONTENT_TYPE_REJECTED")
            if inspection.detected_content_type.lower() != metadata.content_type.lower():
                raise IngestValidationError("INGEST_CONTENT_TYPE_MISMATCH")
            await self._record_inspection(
                business_id=business_id,
                job_id=job_id,
                metadata=metadata,
                detected_content_type=inspection.detected_content_type.lower(),
            )
            await self._set_scanning(business_id, job_id)
            verdict = await self._malware_scanner.scan(
                object_key=object_key, timeout_seconds=self._settings.media_ingest_timeout_seconds
            )
            if verdict == MalwareScanStatus.UNAVAILABLE:
                raise MalwareScanUnavailableError("malware scanner unavailable")
            if verdict != MalwareScanStatus.CLEAN:
                raise IngestValidationError(
                    "MALWARE_SCAN_NOT_CLEAN",
                    quarantine=verdict
                    in {MalwareScanStatus.INFECTED, MalwareScanStatus.INDETERMINATE},
                    scan_status=verdict,
                )
        except (
            StorageUnavailableError,
            ContentInspectionUnavailableError,
            MalwareScanUnavailableError,
        ):
            return await self._fail_transient(business_id, job_id, "INGEST_DEPENDENCY_UNAVAILABLE")
        except StoragePermanentError:
            return await self._fail_permanent(
                business_id, job_id, "INGEST_STORAGE_METADATA_INVALID"
            )
        except IngestValidationError as error:
            return await self._fail_permanent(
                business_id,
                job_id,
                error.code,
                quarantine=error.quarantine,
                scan_status=error.scan_status,
            )
        return await self._complete_clean(business_id, job_id)

    async def _get_metadata(self, object_key: str) -> StoredObjectMetadata:
        return await self._storage.get_object_metadata(object_key=object_key)

    def _validate_metadata(
        self, expected: StoredObjectMetadata, actual: StoredObjectMetadata
    ) -> None:
        if actual.byte_size != expected.byte_size:
            raise IngestValidationError("INGEST_SIZE_MISMATCH")
        if actual.sha256_checksum.lower() != expected.sha256_checksum.lower():
            raise IngestValidationError("UPLOAD_CHECKSUM_MISMATCH")
        if actual.content_type.lower() != expected.content_type.lower():
            raise IngestValidationError("INGEST_CONTENT_TYPE_MISMATCH")
        if actual.content_type.lower() not in self._settings.media_allowed_mime_types:
            raise IngestValidationError("INGEST_CONTENT_TYPE_REJECTED")

    async def _running_job_and_asset(
        self, business_id: UUID, job_id: UUID
    ) -> tuple[BackgroundJob, MediaAsset]:
        job = await self._operations.get_job_for_update(business_id, job_id)
        if job is None or job.job_type != "media.ingest" or job.status != JobStatus.RUNNING:
            raise self._not_found()
        asset = await self._media.get_asset(business_id, job.resource_id, lock=True)
        if asset is None:
            raise self._not_found()
        return job, asset

    async def _record_inspection(
        self,
        *,
        business_id: UUID,
        job_id: UUID,
        metadata: StoredObjectMetadata,
        detected_content_type: str,
    ) -> None:
        async with self._session.begin():
            job, asset = await self._running_job_and_asset(business_id, job_id)
            del job
            inspection = await self._media.get_inspection(business_id, asset.id, lock=True)
            if inspection is None:
                self._media.add(
                    MediaIngestInspection(
                        business_id=business_id,
                        asset_id=asset.id,
                        storage_byte_size=metadata.byte_size,
                        storage_content_type=metadata.content_type.lower(),
                        storage_sha256_checksum=metadata.sha256_checksum.lower(),
                        storage_etag=metadata.etag,
                        detected_content_type=detected_content_type,
                    )
                )

    async def _set_scanning(self, business_id: UUID, job_id: UUID) -> None:
        async with self._session.begin():
            _, asset = await self._running_job_and_asset(business_id, job_id)
            asset.ingest_status = IngestStatus.SCANNING

    async def _complete_clean(self, business_id: UUID, job_id: UUID) -> BackgroundJob:
        async with self._session.begin():
            job, asset = await self._running_job_and_asset(business_id, job_id)
            scan = await self._media.get_malware_scan(business_id, asset.id, lock=True)
            if scan is None:
                self._media.add(
                    MediaMalwareScan(
                        business_id=business_id,
                        asset_id=asset.id,
                        status=MalwareScanStatus.CLEAN,
                        scanner_name="fake-malware-scanner",
                    )
                )
            asset.ingest_status = IngestStatus.READY_FOR_ANALYSIS
            await OperationsService(self._session, self._settings).record_technical_analysis(
                business_id=business_id, asset_id=asset.id, correlation_id=job.correlation_id
            )
            self._finish(job, JobStatus.SUCCEEDED)
            return job

    async def _fail_transient(self, business_id: UUID, job_id: UUID, code: str) -> BackgroundJob:
        async with self._session.begin():
            job, asset = await self._running_job_and_asset(business_id, job_id)
            asset.ingest_status = IngestStatus.FAILED
            if job.attempt_count >= job.max_attempts:
                asset.ingest_status = IngestStatus.DEAD
                self._finish(job, JobStatus.DEAD, code)
            else:
                self._finish(job, JobStatus.FAILED, code)
                job.next_attempt_at = datetime.now(UTC) + timedelta(
                    seconds=min(2**job.attempt_count, 60)
                )
            return job

    async def _fail_permanent(
        self,
        business_id: UUID,
        job_id: UUID,
        code: str,
        *,
        quarantine: bool = False,
        scan_status: MalwareScanStatus | None = None,
    ) -> BackgroundJob:
        async with self._session.begin():
            job, asset = await self._running_job_and_asset(business_id, job_id)
            asset.ingest_status = IngestStatus.REJECTED
            asset.status = MediaAssetStatus.QUARANTINED if quarantine else MediaAssetStatus.REJECTED
            verdict = scan_status or (
                MalwareScanStatus.INFECTED if quarantine else MalwareScanStatus.INDETERMINATE
            )
            scan = await self._media.get_malware_scan(business_id, asset.id, lock=True)
            if scan is None:
                self._media.add(
                    MediaMalwareScan(
                        business_id=business_id,
                        asset_id=asset.id,
                        status=verdict,
                        scanner_name="fake-malware-scanner",
                        safe_error_code=code,
                    )
                )
            self._finish(job, JobStatus.FAILED, code)
            return job

    def _finish(self, job: BackgroundJob, status: JobStatus, error_code: str | None = None) -> None:
        now = datetime.now(UTC)
        job.status = status
        job.finished_at = now
        job.next_attempt_at = None
        job.last_error_code = error_code
        job.last_error_summary = error_code

    @staticmethod
    def _not_found() -> ProblemException:
        return ProblemException(
            status=404,
            code="TENANT_RESOURCE_NOT_FOUND",
            title="Resource not found",
            detail="The requested resource is not available.",
        )
