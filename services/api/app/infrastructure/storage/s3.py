"""S3-compatible multipart storage adapter: MinIO locally, S3/R2 in production.

The adapter is the only place that knows a provider exists. It signs requests with SigV4
directly over ``httpx`` rather than pulling a synchronous vendor SDK into an async request
path, and it never returns, logs, or raises provider URLs, credentials, or response bodies.

Multipart state mapping. The port identifies an upload by the server-generated
``storage_upload_id`` persisted in ``media_upload_sessions.storage_upload_id``
(``String(128)``). A provider ``UploadId`` cannot go in that column: AWS values routinely
exceed 128 characters and widening the column needs a migration this slice may not add.
So ``create_upload`` writes a small server-owned control object at
``_control/uploads/{storage_upload_id}.json`` holding the object key and provider upload
id, and the later part/complete/cancel calls resolve through it. See ADR-008.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import re
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote, urlsplit
from xml.etree import ElementTree

import httpx
import structlog

from app.core.config import Settings
from app.modules.media.storage import (
    CompletedPart,
    StoragePermanentError,
    StorageUnavailableError,
    StoredObjectMetadata,
    UploadPartInstruction,
)

logger = structlog.get_logger(__name__)

_ALGORITHM = "AWS4-HMAC-SHA256"
_UNSIGNED_PAYLOAD = "UNSIGNED-PAYLOAD"
_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
_CHUNK_BYTES = 1_048_576
_CONTROL_PREFIX = "_control/uploads/"
_MAX_XML_BYTES = 1_048_576
_S3_MAX_PART_NUMBER = 10_000
_SHA256_METADATA = "x-amz-meta-sha256"

_SAFE_UPLOAD_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_SAFE_OBJECT_KEY = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._/-]{0,1023}$")
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
_ETAG = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
# Transient at the HTTP layer: throttling, gateway churn, provider outage.
_TRANSIENT_STATUS = frozenset({401, 403, 408, 429, 500, 502, 503, 504, 507, 509})


class _ControlRecord:
    """Server-owned mapping from the persisted session id to provider multipart state."""

    __slots__ = ("content_type", "object_key", "upload_id")

    def __init__(self, *, object_key: str, upload_id: str, content_type: str) -> None:
        self.object_key = object_key
        self.upload_id = upload_id
        self.content_type = content_type


@dataclass(frozen=True)
class _HeadResult:
    byte_size: int
    content_type: str
    etag: str
    sha256_checksum: str | None


@dataclass(frozen=True)
class _ObservedPart:
    part_number: int
    etag: str


@dataclass(frozen=True)
class _Endpoint:
    """One provider origin plus the addressing style used to build canonical URIs."""

    scheme: str
    netloc: str
    bucket: str
    path_style: bool

    @classmethod
    def parse(cls, origin: str, *, bucket: str, path_style: bool) -> _Endpoint:
        parsed = urlsplit(origin)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise StoragePermanentError("storage endpoint is not usable")
        return cls(scheme=parsed.scheme, netloc=parsed.netloc, bucket=bucket, path_style=path_style)

    @property
    def host(self) -> str:
        return self.netloc if self.path_style else f"{self.bucket}.{self.netloc}"

    def path(self, object_key: str) -> str:
        # S3 canonical URIs are single-encoded and keep the path separators intact.
        encoded = quote(object_key, safe="/-._~")
        return f"/{self.bucket}/{encoded}" if self.path_style else f"/{encoded}"

    def url(self, object_key: str, query: Mapping[str, str] | None = None) -> str:
        suffix = f"?{_canonical_query(query)}" if query else ""
        return f"{self.scheme}://{self.host}{self.path(object_key)}{suffix}"


def _canonical_query(params: Mapping[str, str]) -> str:
    return "&".join(
        f"{quote(key, safe='-._~')}={quote(value, safe='-._~')}"
        for key, value in sorted(params.items())
    )


def _sign(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()


def _signing_key(secret: str, date_stamp: str, region: str) -> bytes:
    initial = _sign(f"AWS4{secret}".encode(), date_stamp)
    return _sign(_sign(_sign(initial, region), "s3"), "aws4_request")


class _Signer:
    """SigV4 for one origin. Header auth for server calls, query auth for client URLs."""

    def __init__(
        self, *, endpoint: _Endpoint, access_key_id: str, secret_access_key: str, region: str
    ) -> None:
        self._endpoint = endpoint
        self._access_key_id = access_key_id
        self._secret_access_key = secret_access_key
        self._region = region

    @property
    def endpoint(self) -> _Endpoint:
        return self._endpoint

    def _credential_scope(self, date_stamp: str) -> str:
        return f"{date_stamp}/{self._region}/s3/aws4_request"

    def _signature(self, *, canonical_request: str, amz_date: str, date_stamp: str) -> str:
        string_to_sign = "\n".join(
            (
                _ALGORITHM,
                amz_date,
                self._credential_scope(date_stamp),
                hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
            )
        )
        return hmac.new(
            _signing_key(self._secret_access_key, date_stamp, self._region),
            string_to_sign.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def headers(
        self,
        *,
        method: str,
        object_key: str,
        query: Mapping[str, str],
        payload_sha256: str,
        now: datetime,
        extra: Mapping[str, str] | None = None,
    ) -> dict[str, str]:
        amz_date, date_stamp = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
        headers = {
            "host": self._endpoint.host,
            "x-amz-content-sha256": payload_sha256,
            "x-amz-date": amz_date,
        }
        headers.update({key.lower(): value for key, value in (extra or {}).items()})
        names = sorted(headers)
        canonical_request = "\n".join(
            (
                method,
                self._endpoint.path(object_key),
                _canonical_query(query),
                "".join(f"{name}:{headers[name].strip()}\n" for name in names),
                ";".join(names),
                payload_sha256,
            )
        )
        signature = self._signature(
            canonical_request=canonical_request, amz_date=amz_date, date_stamp=date_stamp
        )
        headers["authorization"] = (
            f"{_ALGORITHM} Credential={self._access_key_id}/{self._credential_scope(date_stamp)}, "
            f"SignedHeaders={';'.join(names)}, Signature={signature}"
        )
        return headers

    def presign(
        self,
        *,
        method: str,
        object_key: str,
        query: Mapping[str, str],
        expires_seconds: int,
        now: datetime,
    ) -> str:
        amz_date, date_stamp = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
        params = dict(query)
        params.update(
            {
                "X-Amz-Algorithm": _ALGORITHM,
                "X-Amz-Credential": f"{self._access_key_id}/{self._credential_scope(date_stamp)}",
                "X-Amz-Date": amz_date,
                "X-Amz-Expires": str(expires_seconds),
                "X-Amz-SignedHeaders": "host",
            }
        )
        canonical_request = "\n".join(
            (
                method,
                self._endpoint.path(object_key),
                _canonical_query(params),
                f"host:{self._endpoint.host}\n",
                "host",
                _UNSIGNED_PAYLOAD,
            )
        )
        signature = self._signature(
            canonical_request=canonical_request, amz_date=amz_date, date_stamp=date_stamp
        )
        return f"{self._endpoint.url(object_key, params)}&X-Amz-Signature={signature}"


def _validate_object_key(object_key: str) -> str:
    if not _SAFE_OBJECT_KEY.fullmatch(object_key) or ".." in object_key:
        raise StoragePermanentError("storage object key is not usable")
    return object_key


def _validate_upload_id(storage_upload_id: str) -> str:
    if not _SAFE_UPLOAD_ID.fullmatch(storage_upload_id):
        raise StoragePermanentError("storage upload identifier is not usable")
    return storage_upload_id


def _control_key(storage_upload_id: str) -> str:
    return f"{_CONTROL_PREFIX}{_validate_upload_id(storage_upload_id)}.json"


def _normalized_etag(value: str) -> str:
    return value.strip().strip('"').lower()


def _strip_namespace(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _parse_xml(payload: bytes) -> ElementTree.Element:
    """Parse a bounded provider response; oversize or malformed bodies are permanent."""

    if not payload or len(payload) > _MAX_XML_BYTES:
        raise StoragePermanentError("storage response was not usable")
    try:
        return ElementTree.fromstring(payload)
    except ElementTree.ParseError as error:
        raise StoragePermanentError("storage response was not usable") from error


def _find_text(element: ElementTree.Element, name: str) -> str | None:
    for child in element:
        if _strip_namespace(child.tag) == name:
            return (child.text or "").strip()
    return None


class S3MultipartStorage:
    """Provider adapter for the direct-upload byte path.

    Every method is stateless with respect to the process: the API and the workers can hold
    independent instances against the same bucket.
    """

    def __init__(
        self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        bucket = settings.s3_bucket
        endpoint = _Endpoint.parse(
            settings.s3_endpoint_url, bucket=bucket, path_style=settings.s3_force_path_style
        )
        presign_endpoint = _Endpoint.parse(
            settings.s3_presign_endpoint_url or settings.s3_endpoint_url,
            bucket=bucket,
            path_style=settings.s3_force_path_style,
        )
        credentials = {
            "access_key_id": settings.s3_access_key_id.get_secret_value(),
            "secret_access_key": settings.s3_secret_access_key.get_secret_value(),
            "region": settings.s3_region,
        }
        self._signer = _Signer(endpoint=endpoint, **credentials)
        self._presigner = _Signer(endpoint=presign_endpoint, **credentials)
        self._transport = transport
        self._request_timeout = settings.s3_request_timeout_seconds
        self._verification_timeout = settings.s3_verification_timeout_seconds
        self._presign_ttl = settings.s3_presign_ttl_seconds
        self._max_part_count = settings.media_max_parts
        # Streaming verification is the fallback when a provider reports no server-side
        # digest, so the ceiling must cover every object kind this system writes.
        self._max_verification_bytes = max(
            settings.media_max_bytes,
            settings.media_max_derivative_bytes,
            settings.media_max_extracted_audio_bytes,
        )

    # ------------------------------------------------------------------ port surface

    async def create_upload(
        self,
        *,
        storage_upload_id: str,
        object_key: str,
        content_type: str,
        expires_at: datetime,
        part_numbers: tuple[int, ...],
    ) -> tuple[UploadPartInstruction, ...]:
        _validate_object_key(object_key)
        _validate_upload_id(storage_upload_id)
        upload_id = await self._create_multipart(object_key, content_type)
        try:
            await self._put_bytes(
                _control_key(storage_upload_id),
                json.dumps(
                    {
                        "object_key": object_key,
                        "upload_id": upload_id,
                        "content_type": content_type,
                    }
                ).encode("utf-8"),
                content_type="application/json",
            )
        except (StorageUnavailableError, StoragePermanentError):
            # Never leave a provider-side multipart upload without its control record.
            await self._abort_quietly(object_key, upload_id)
            raise
        return self._instructions(object_key, upload_id, expires_at, part_numbers)

    async def create_part_urls(
        self, *, storage_upload_id: str, expires_at: datetime, part_numbers: tuple[int, ...]
    ) -> tuple[UploadPartInstruction, ...]:
        control = await self._get_control(storage_upload_id)
        return self._instructions(control.object_key, control.upload_id, expires_at, part_numbers)

    async def complete_upload(
        self, *, storage_upload_id: str, parts: tuple[CompletedPart, ...]
    ) -> StoredObjectMetadata:
        control = await self._get_control(storage_upload_id)
        observed = await self._list_parts(control.object_key, control.upload_id)
        self._require_declared_parts_match(parts, observed)
        # Finalize from the server-observed inventory, never from the client's copy.
        await self._complete_multipart(control.object_key, control.upload_id, observed)
        metadata = await self.get_object_metadata(object_key=control.object_key)
        await self._delete_quietly(_control_key(storage_upload_id))
        return metadata

    async def get_object_metadata(self, *, object_key: str) -> StoredObjectMetadata:
        _validate_object_key(object_key)
        head = await self._head(object_key)
        checksum = head.sha256_checksum or await self._streamed_sha256(object_key, head.byte_size)
        return StoredObjectMetadata(
            byte_size=head.byte_size,
            content_type=head.content_type,
            sha256_checksum=checksum,
            etag=head.etag,
        )

    async def persist_file(
        self, *, object_key: str, source_path: Path, content_type: str
    ) -> StoredObjectMetadata:
        """Store a worker-produced file, recording the server-computed digest as metadata."""

        _validate_object_key(object_key)
        existing = await self._head_optional(object_key)
        if existing is not None:
            checksum = existing.sha256_checksum or await self._streamed_sha256(
                object_key, existing.byte_size
            )
            return StoredObjectMetadata(
                byte_size=existing.byte_size,
                content_type=existing.content_type,
                sha256_checksum=checksum,
                etag=existing.etag,
            )
        checksum, byte_size = await asyncio.to_thread(_hash_file, source_path)
        if byte_size == 0:
            raise StoragePermanentError("storage persistence rejected empty file")
        if byte_size > self._max_verification_bytes:
            raise StoragePermanentError("storage persistence rejected oversize file")
        etag = await self._put_stream(
            object_key,
            source_path,
            byte_size=byte_size,
            payload_sha256=checksum,
            content_type=content_type,
        )
        return StoredObjectMetadata(
            byte_size=byte_size, content_type=content_type, sha256_checksum=checksum, etag=etag
        )

    async def cancel_upload(self, *, storage_upload_id: str) -> None:
        control = await self._get_control(storage_upload_id)
        await self._abort_multipart(control.object_key, control.upload_id)
        await self._delete_quietly(_control_key(storage_upload_id))

    # ------------------------------------------------------------- presigned part URLs

    def _instructions(
        self,
        object_key: str,
        upload_id: str,
        expires_at: datetime,
        part_numbers: tuple[int, ...],
    ) -> tuple[UploadPartInstruction, ...]:
        if not part_numbers or len(part_numbers) > self._max_part_count:
            raise StoragePermanentError("storage part selection is not usable")
        if any(
            number < 1 or number > min(self._max_part_count, _S3_MAX_PART_NUMBER)
            for number in part_numbers
        ):
            raise StoragePermanentError("storage part selection is not usable")
        now = datetime.now(UTC)
        remaining = int((expires_at - now).total_seconds())
        if remaining <= 0:
            # An expired session must never receive fresh upload capability.
            raise StorageUnavailableError("upload window is closed")
        expires_seconds = min(remaining, self._presign_ttl)
        instructions = tuple(
            UploadPartInstruction(
                number,
                self._presigner.presign(
                    method="PUT",
                    object_key=object_key,
                    query={"partNumber": str(number), "uploadId": upload_id},
                    expires_seconds=expires_seconds,
                    now=now,
                ),
            )
            for number in part_numbers
        )
        # Safe telemetry only: no URL, no signature, no credential, no object key.
        logger.info(
            "storage_part_urls_issued",
            part_count=len(instructions),
            expires_in_seconds=expires_seconds,
        )
        return instructions

    # ------------------------------------------------------------------ provider calls

    async def _create_multipart(self, object_key: str, content_type: str) -> str:
        response = await self._request(
            "POST",
            object_key,
            query={"uploads": ""},
            payload=b"",
            extra_headers={"content-type": content_type},
        )
        upload_id = _find_text(_parse_xml(response.content), "UploadId")
        if not upload_id or not _SAFE_UPLOAD_ID.fullmatch(upload_id):
            raise StoragePermanentError("storage did not return a usable upload identifier")
        return upload_id

    async def _list_parts(self, object_key: str, upload_id: str) -> tuple[_ObservedPart, ...]:
        response = await self._request(
            "GET", object_key, query={"uploadId": upload_id, "max-parts": "10000"}, payload=b""
        )
        root = _parse_xml(response.content)
        if (_find_text(root, "IsTruncated") or "false").lower() == "true":
            raise StoragePermanentError("storage reported more parts than this system allows")
        observed: list[_ObservedPart] = []
        for child in root:
            if _strip_namespace(child.tag) != "Part":
                continue
            number, etag = _find_text(child, "PartNumber"), _find_text(child, "ETag")
            if not number or not number.isdigit() or not etag:
                raise StoragePermanentError("storage returned an unusable part listing")
            observed.append(_ObservedPart(int(number), _normalized_etag(etag)))
        if not observed:
            raise StoragePermanentError("storage holds no parts for this upload")
        return tuple(sorted(observed, key=lambda part: part.part_number))

    @staticmethod
    def _require_declared_parts_match(
        declared: tuple[CompletedPart, ...], observed: tuple[_ObservedPart, ...]
    ) -> None:
        """Compare the client's declaration against the provider's inventory."""

        declared_pairs = {(part.part_number, _normalized_etag(part.etag)) for part in declared}
        observed_pairs = {(part.part_number, part.etag) for part in observed}
        if declared_pairs != observed_pairs:
            raise StoragePermanentError("stored parts do not match the completion request")

    async def _complete_multipart(
        self, object_key: str, upload_id: str, parts: tuple[_ObservedPart, ...]
    ) -> None:
        body = "".join(
            f"<Part><PartNumber>{part.part_number}</PartNumber>"
            f"<ETag>&quot;{part.etag}&quot;</ETag></Part>"
            for part in parts
            if _ETAG.fullmatch(part.etag)
        )
        if body.count("<Part>") != len(parts):
            raise StoragePermanentError("storage returned an unusable part listing")
        response = await self._request(
            "POST",
            object_key,
            query={"uploadId": upload_id},
            payload=f"<CompleteMultipartUpload>{body}</CompleteMultipartUpload>".encode(),
            extra_headers={"content-type": "application/xml"},
        )
        # S3 can report a failure inside a 200 body once it starts streaming the response.
        root = _parse_xml(response.content)
        if _strip_namespace(root.tag) != "CompleteMultipartUploadResult":
            raise StoragePermanentError("storage did not finalize the upload")

    async def _abort_multipart(self, object_key: str, upload_id: str) -> None:
        await self._request("DELETE", object_key, query={"uploadId": upload_id}, payload=b"")

    async def _abort_quietly(self, object_key: str, upload_id: str) -> None:
        try:
            await self._abort_multipart(object_key, upload_id)
        except (StorageUnavailableError, StoragePermanentError):
            logger.warning("storage_multipart_abort_failed")

    async def _delete_quietly(self, object_key: str) -> None:
        try:
            await self._request("DELETE", object_key, query={}, payload=b"")
        except (StorageUnavailableError, StoragePermanentError):
            logger.warning("storage_control_object_delete_failed")

    async def _get_control(self, storage_upload_id: str) -> _ControlRecord:
        response = await self._request(
            "GET", _control_key(storage_upload_id), query={}, payload=b""
        )
        try:
            document = json.loads(response.content)
        except ValueError as error:
            raise StoragePermanentError("storage control record is not usable") from error
        if not isinstance(document, dict):
            raise StoragePermanentError("storage control record is not usable")
        object_key = document.get("object_key")
        upload_id = document.get("upload_id")
        content_type = document.get("content_type")
        if not isinstance(object_key, str) or not isinstance(upload_id, str):
            raise StoragePermanentError("storage control record is not usable")
        return _ControlRecord(
            object_key=_validate_object_key(object_key),
            upload_id=_validate_upload_id(upload_id),
            content_type=content_type if isinstance(content_type, str) else "",
        )

    async def _head(self, object_key: str) -> _HeadResult:
        head = await self._head_optional(object_key)
        if head is None:
            raise StoragePermanentError("storage object is missing")
        return head

    async def _head_optional(self, object_key: str) -> _HeadResult | None:
        response = await self._request(
            "HEAD", object_key, query={}, payload=b"", allow_missing=True
        )
        if response.status_code == 404:
            return None
        try:
            byte_size = int(response.headers.get("content-length", ""))
        except ValueError as error:
            raise StoragePermanentError("storage object metadata is not usable") from error
        if byte_size < 0:
            raise StoragePermanentError("storage object metadata is not usable")
        declared = _normalized_etag(response.headers.get(_SHA256_METADATA, ""))
        return _HeadResult(
            byte_size=byte_size,
            content_type=(response.headers.get("content-type") or "").lower(),
            etag=_normalized_etag(response.headers.get("etag", "")),
            sha256_checksum=declared if _SHA256_HEX.fullmatch(declared) else None,
        )

    async def _streamed_sha256(self, object_key: str, expected_size: int) -> str:
        """Observe the object's digest server-side instead of trusting any declaration."""

        if expected_size > self._max_verification_bytes:
            raise StoragePermanentError("storage object exceeds the verification limit")
        digest, total = hashlib.sha256(), 0
        url = self._signer.endpoint.url(object_key)
        headers = self._signer.headers(
            method="GET",
            object_key=object_key,
            query={},
            payload_sha256=_EMPTY_SHA256,
            now=datetime.now(UTC),
        )
        async with self._client(self._verification_timeout) as client:
            try:
                async with client.stream("GET", url, headers=headers) as response:
                    if response.status_code != 200:
                        await response.aread()
                        raise self._mapped_error(response.status_code)
                    async for chunk in response.aiter_bytes(_CHUNK_BYTES):
                        total += len(chunk)
                        if total > expected_size:
                            raise StoragePermanentError("storage object size does not match")
                        digest.update(chunk)
            except httpx.HTTPError as error:
                raise StorageUnavailableError("storage request failed") from error
        if total != expected_size:
            raise StoragePermanentError("storage object size does not match")
        return digest.hexdigest()

    async def _put_bytes(self, object_key: str, payload: bytes, *, content_type: str) -> None:
        await self._request(
            "PUT",
            object_key,
            query={},
            payload=payload,
            extra_headers={"content-type": content_type},
        )

    async def _put_stream(
        self,
        object_key: str,
        source_path: Path,
        *,
        byte_size: int,
        payload_sha256: str,
        content_type: str,
    ) -> str:
        extra = {
            "content-type": content_type,
            "content-length": str(byte_size),
            _SHA256_METADATA: payload_sha256,
        }
        headers = self._signer.headers(
            method="PUT",
            object_key=object_key,
            query={},
            payload_sha256=payload_sha256,
            now=datetime.now(UTC),
            extra=extra,
        )
        async with self._client(self._verification_timeout) as client:
            try:
                # httpx keeps the body unchunked because Content-Length is already set.
                response = await client.request(
                    "PUT",
                    self._signer.endpoint.url(object_key),
                    headers=headers,
                    content=_file_chunks(source_path),
                )
            except httpx.HTTPError as error:
                raise StorageUnavailableError("storage request failed") from error
        if response.status_code != 200:
            raise self._mapped_error(response.status_code)
        return _normalized_etag(response.headers.get("etag", ""))

    async def _request(
        self,
        method: str,
        object_key: str,
        *,
        query: Mapping[str, str],
        payload: bytes,
        extra_headers: Mapping[str, str] | None = None,
        allow_missing: bool = False,
    ) -> httpx.Response:
        extra = dict(extra_headers or {})
        if payload:
            extra["content-length"] = str(len(payload))
        headers = self._signer.headers(
            method=method,
            object_key=object_key,
            query=query,
            payload_sha256=hashlib.sha256(payload).hexdigest(),
            now=datetime.now(UTC),
            extra=extra,
        )
        async with self._client(self._request_timeout) as client:
            try:
                response = await client.request(
                    method,
                    self._signer.endpoint.url(object_key, query),
                    headers=headers,
                    content=payload or None,
                )
            except httpx.HTTPError as error:
                raise StorageUnavailableError("storage request failed") from error
        if response.status_code in {200, 204} or (allow_missing and response.status_code == 404):
            return response
        raise self._mapped_error(response.status_code)

    @asynccontextmanager
    async def _client(self, timeout: float) -> AsyncIterator[httpx.AsyncClient]:
        """One client per operation; storage calls are per-upload, not per-byte.

        A pooled client owned by the application lifespan is the natural next step, and it
        needs shutdown plumbing in both composition roots. Recorded in ADR-008.
        """

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout), transport=self._transport, follow_redirects=False
        ) as client:
            yield client

    @staticmethod
    def _mapped_error(status_code: int) -> StorageUnavailableError | StoragePermanentError:
        """Map a provider status to a neutral port error; provider bodies never escape."""

        logger.warning("storage_request_rejected", status_code=status_code)
        if status_code in _TRANSIENT_STATUS:
            return StorageUnavailableError("storage request was rejected")
        return StoragePermanentError("storage request was rejected")


def _hash_file(source_path: Path) -> tuple[str, int]:
    digest, size = hashlib.sha256(), 0
    try:
        with source_path.open("rb") as handle:
            while chunk := handle.read(_CHUNK_BYTES):
                digest.update(chunk)
                size += len(chunk)
    except OSError as error:
        raise StorageUnavailableError("storage persistence unavailable") from error
    return digest.hexdigest(), size


async def _file_chunks(source_path: Path) -> AsyncIterator[bytes]:
    """Stream a worker file without holding it in memory or blocking the event loop."""

    handle = await asyncio.to_thread(source_path.open, "rb")
    try:
        while chunk := await asyncio.to_thread(handle.read, _CHUNK_BYTES):
            yield chunk
    finally:
        await asyncio.to_thread(handle.close)
