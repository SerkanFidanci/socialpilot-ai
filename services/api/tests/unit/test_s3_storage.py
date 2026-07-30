"""Contract tests for the S3-compatible adapter against a scripted provider.

These run without network or credentials. The real-provider proof lives in
`tests/integration/test_media_uploads_minio.py`.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import structlog
from pydantic import SecretStr

from app.core.config import Settings
from app.infrastructure.storage import create_storage
from app.infrastructure.storage.fake import FakeMultipartStorage
from app.infrastructure.storage.s3 import S3MultipartStorage
from app.modules.media.storage import (
    CompletedPart,
    StoragePermanentError,
    StorageUnavailableError,
)

OBJECT_KEY = "tenant/11111111-1111-1111-1111-111111111111/media/asset/original/abc123"
SESSION_ID = "0123456789abcdef0123456789abcdef"
CONTROL_KEY = f"_control/uploads/{SESSION_ID}.json"
NAMESPACE = "http://s3.amazonaws.com/doc/2006-03-01/"
PAYLOAD = b"a" * 4096
PAYLOAD_SHA256 = hashlib.sha256(PAYLOAD).hexdigest()


def settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_url": "postgresql+asyncpg://user:password@localhost:5432/socialpilot",
        "redis_url": "redis://localhost:6379/0",
        "celery_broker_url": "redis://localhost:6379/1",
        "celery_result_backend": "redis://localhost:6379/2",
        "storage_adapter": "s3",
        "s3_endpoint_url": "http://storage.internal:9000",
        "s3_presign_endpoint_url": "http://storage.public:9000",
        "s3_bucket": "socialpilot-media",
        "s3_access_key_id": SecretStr("test-access-key"),
        "s3_secret_access_key": SecretStr("test-secret-key"),
    }
    values.update(overrides)
    return Settings.model_validate(values)


class ScriptedS3:
    """A minimal S3 stand-in that records every request it served."""

    def __init__(self, *, namespaced: bool = True) -> None:
        self.requests: list[httpx.Request] = []
        self.objects: dict[str, tuple[bytes, dict[str, str]]] = {}
        self.parts: list[tuple[int, str]] = [(1, "aaa111"), (2, "bbb222")]
        self.status_overrides: dict[str, int] = {}
        self.completed = False
        self.aborted = False
        self._prefix = f"{{{NAMESPACE}}}" if namespaced else ""

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def _xml(self, body: str) -> bytes:
        attribute = f' xmlns="{NAMESPACE}"' if self._prefix else ""
        return body.replace("<ROOT", f"<ROOT{attribute}", 1).encode("utf-8")

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = urlsplit(str(request.url)).path
        key = path.removeprefix("/socialpilot-media/")
        query = parse_qs(request.url.query.decode("utf-8"), keep_blank_values=True)
        override = self.status_overrides.get(f"{request.method} {key}")
        if override is not None:
            return httpx.Response(override)

        if request.method == "POST" and "uploads" in query:
            return httpx.Response(
                200,
                content=self._xml(
                    "<ROOT><Bucket>socialpilot-media</Bucket><UploadId>provider-upload-1</UploadId>"
                    "</ROOT>".replace("<ROOT>", "<InitiateMultipartUploadResult>").replace(
                        "</ROOT>", "</InitiateMultipartUploadResult>"
                    )
                ),
            )
        if request.method == "GET" and "uploadId" in query:
            entries = "".join(
                f"<Part><PartNumber>{number}</PartNumber><ETag>&quot;{etag}&quot;</ETag>"
                f"<Size>1024</Size></Part>"
                for number, etag in self.parts
            )
            return httpx.Response(
                200,
                content=self._xml(
                    f"<ROOT><IsTruncated>false</IsTruncated>{entries}</ROOT>".replace(
                        "<ROOT>", "<ListPartsResult>"
                    ).replace("</ROOT>", "</ListPartsResult>")
                ),
            )
        if request.method == "POST" and "uploadId" in query:
            self.completed = True
            self.objects[key] = (PAYLOAD, {"content-type": "video/mp4"})
            return httpx.Response(
                200,
                content=self._xml(
                    "<ROOT><ETag>&quot;final-etag&quot;</ETag></ROOT>".replace(
                        "<ROOT>", "<CompleteMultipartUploadResult>"
                    ).replace("</ROOT>", "</CompleteMultipartUploadResult>")
                ),
            )
        if request.method == "DELETE":
            self.aborted = self.aborted or "uploadId" in query
            self.objects.pop(key, None)
            return httpx.Response(204)
        if request.method == "PUT":
            headers = {
                name: value
                for name, value in request.headers.items()
                if name.startswith("x-amz-meta-") or name == "content-type"
            }
            self.objects[key] = (request.content, headers)
            return httpx.Response(200, headers={"etag": '"stored-etag"'})
        if request.method in {"HEAD", "GET"}:
            stored = self.objects.get(key)
            if stored is None:
                return httpx.Response(404)
            body, headers = stored
            response_headers = {
                "content-length": str(len(body)),
                "content-type": headers.get("content-type", "binary/octet-stream"),
                "etag": '"stored-etag"',
                **{
                    name: value for name, value in headers.items() if name.startswith("x-amz-meta-")
                },
            }
            if request.method == "HEAD":
                return httpx.Response(200, headers=response_headers)
            return httpx.Response(200, content=body, headers=response_headers)
        return httpx.Response(405)

    def seed_control(self) -> None:
        self.objects[CONTROL_KEY] = (
            json.dumps(
                {
                    "object_key": OBJECT_KEY,
                    "upload_id": "provider-upload-1",
                    "content_type": "video/mp4",
                }
            ).encode("utf-8"),
            {"content-type": "application/json"},
        )


def adapter(provider: ScriptedS3, **overrides: object) -> S3MultipartStorage:
    return S3MultipartStorage(settings(**overrides), transport=provider.transport())


def later(seconds: int = 900) -> datetime:
    return datetime.now(UTC) + timedelta(seconds=seconds)


async def test_create_upload_presigns_parts_for_the_client_endpoint() -> None:
    provider = ScriptedS3()

    instructions = await adapter(provider).create_upload(
        storage_upload_id=SESSION_ID,
        object_key=OBJECT_KEY,
        content_type="video/mp4",
        expires_at=later(),
        part_numbers=(1, 2),
    )

    assert [instruction.part_number for instruction in instructions] == [1, 2]
    parsed = urlsplit(instructions[0].upload_url)
    query = parse_qs(parsed.query)
    # Signed for the client-reachable host, not the server-side endpoint.
    assert parsed.netloc == "storage.public:9000"
    assert parsed.path == f"/socialpilot-media/{OBJECT_KEY}"
    assert query["X-Amz-Algorithm"] == ["AWS4-HMAC-SHA256"]
    assert query["X-Amz-Credential"][0].startswith("test-access-key/")
    assert query["X-Amz-Credential"][0].endswith("/us-east-1/s3/aws4_request")
    assert query["X-Amz-SignedHeaders"] == ["host"]
    assert query["partNumber"] == ["1"] and query["uploadId"] == ["provider-upload-1"]
    assert len(query["X-Amz-Signature"][0]) == 64
    # The provider call itself went to the internal endpoint.
    assert provider.requests[0].url.host == "storage.internal"
    control = json.loads(provider.objects[CONTROL_KEY][0])
    assert control == {
        "object_key": OBJECT_KEY,
        "upload_id": "provider-upload-1",
        "content_type": "video/mp4",
    }


async def test_create_upload_stamps_the_declared_content_type_on_the_object() -> None:
    provider = ScriptedS3()

    await adapter(provider).create_upload(
        storage_upload_id=SESSION_ID,
        object_key=OBJECT_KEY,
        content_type="video/quicktime",
        expires_at=later(),
        part_numbers=(1,),
    )

    assert provider.requests[0].headers["content-type"] == "video/quicktime"


async def test_part_urls_expire_with_the_session_and_never_outlive_the_ttl() -> None:
    provider = ScriptedS3()
    provider.seed_control()
    storage = adapter(provider, s3_presign_ttl_seconds=600)

    short = await storage.create_part_urls(
        storage_upload_id=SESSION_ID, expires_at=later(120), part_numbers=(1,)
    )
    long = await storage.create_part_urls(
        storage_upload_id=SESSION_ID, expires_at=later(3_000), part_numbers=(1,)
    )

    assert int(parse_qs(urlsplit(short[0].upload_url).query)["X-Amz-Expires"][0]) <= 120
    assert int(parse_qs(urlsplit(long[0].upload_url).query)["X-Amz-Expires"][0]) == 600


async def test_expired_session_gets_no_part_url() -> None:
    provider = ScriptedS3()
    provider.seed_control()

    with pytest.raises(StorageUnavailableError):
        await adapter(provider).create_part_urls(
            storage_upload_id=SESSION_ID, expires_at=later(-1), part_numbers=(1,)
        )

    assert all(request.method == "GET" for request in provider.requests)


async def test_part_numbers_beyond_the_configured_ceiling_are_refused() -> None:
    provider = ScriptedS3()
    provider.seed_control()

    with pytest.raises(StoragePermanentError):
        await adapter(provider, media_max_parts=4).create_part_urls(
            storage_upload_id=SESSION_ID, expires_at=later(), part_numbers=(5,)
        )


@pytest.mark.parametrize("namespaced", [True, False])
async def test_completion_verifies_the_object_it_finalized(namespaced: bool) -> None:
    provider = ScriptedS3(namespaced=namespaced)
    provider.seed_control()

    metadata = await adapter(provider).complete_upload(
        storage_upload_id=SESSION_ID,
        parts=(CompletedPart(1, '"AAA111"'), CompletedPart(2, "bbb222")),
    )

    assert provider.completed
    # The digest is observed from the stored bytes, never taken from the request.
    assert metadata.sha256_checksum == PAYLOAD_SHA256
    assert metadata.byte_size == len(PAYLOAD)
    assert metadata.content_type == "video/mp4"
    assert metadata.etag == "stored-etag"
    # The control object is cleaned up once the upload is finalized.
    assert CONTROL_KEY not in provider.objects


async def test_completion_finalizes_from_the_provider_part_inventory() -> None:
    provider = ScriptedS3()
    provider.seed_control()

    await adapter(provider).complete_upload(
        storage_upload_id=SESSION_ID, parts=(CompletedPart(1, "aaa111"), CompletedPart(2, "bbb222"))
    )

    finalize = next(
        request
        for request in provider.requests
        if request.method == "POST" and b"uploadId" in request.url.query
    )
    assert b"<ETag>&quot;aaa111&quot;</ETag>" in finalize.content
    assert b"<ETag>&quot;bbb222&quot;</ETag>" in finalize.content


@pytest.mark.parametrize(
    "declared",
    [
        (CompletedPart(1, "aaa111"),),
        (CompletedPart(1, "aaa111"), CompletedPart(2, "wrong")),
        (CompletedPart(1, "aaa111"), CompletedPart(2, "bbb222"), CompletedPart(3, "ccc333")),
    ],
)
async def test_completion_rejects_a_declaration_that_storage_contradicts(
    declared: tuple[CompletedPart, ...],
) -> None:
    provider = ScriptedS3()
    provider.seed_control()

    with pytest.raises(StoragePermanentError):
        await adapter(provider).complete_upload(storage_upload_id=SESSION_ID, parts=declared)

    assert not provider.completed


def _with_reported_size(provider: ScriptedS3, byte_size: int) -> S3MultipartStorage:
    """Let the provider claim a size the stored bytes do not match."""

    def relabelling(request: httpx.Request) -> httpx.Response:
        response = provider.handle(request)
        if request.method == "HEAD" and response.status_code == 200:
            response.headers["content-length"] = str(byte_size)
        return response

    return S3MultipartStorage(settings(), transport=httpx.MockTransport(relabelling))


async def test_metadata_size_disagreement_is_permanent() -> None:
    provider = ScriptedS3()
    provider.objects[OBJECT_KEY] = (PAYLOAD, {"content-type": "video/mp4"})

    with pytest.raises(StoragePermanentError):
        await _with_reported_size(provider, len(PAYLOAD) + 1).get_object_metadata(
            object_key=OBJECT_KEY
        )


async def test_object_larger_than_the_verification_ceiling_is_never_streamed() -> None:
    provider = ScriptedS3()
    provider.objects[OBJECT_KEY] = (PAYLOAD, {"content-type": "video/mp4"})
    ceiling = max(
        settings().media_max_bytes,
        settings().media_max_derivative_bytes,
        settings().media_max_extracted_audio_bytes,
    )

    with pytest.raises(StoragePermanentError):
        await _with_reported_size(provider, ceiling + 1).get_object_metadata(object_key=OBJECT_KEY)

    # Refused on the metadata read; the body was never pulled through the process.
    assert all(request.method != "GET" for request in provider.requests)


async def test_missing_object_is_permanent_and_provider_outage_is_transient() -> None:
    provider = ScriptedS3()

    with pytest.raises(StoragePermanentError):
        await adapter(provider).get_object_metadata(object_key=OBJECT_KEY)

    provider.status_overrides[f"HEAD {OBJECT_KEY}"] = 503
    with pytest.raises(StorageUnavailableError):
        await adapter(provider).get_object_metadata(object_key=OBJECT_KEY)


async def test_network_failure_is_transient() -> None:
    def failing(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    storage = S3MultipartStorage(settings(), transport=httpx.MockTransport(failing))

    with pytest.raises(StorageUnavailableError):
        await storage.get_object_metadata(object_key=OBJECT_KEY)


async def test_control_write_failure_aborts_the_provider_upload() -> None:
    provider = ScriptedS3()
    provider.status_overrides[f"PUT {CONTROL_KEY}"] = 500

    with pytest.raises(StorageUnavailableError):
        await adapter(provider).create_upload(
            storage_upload_id=SESSION_ID,
            object_key=OBJECT_KEY,
            content_type="video/mp4",
            expires_at=later(),
            part_numbers=(1,),
        )

    assert provider.aborted


async def test_cancel_aborts_the_upload_and_drops_the_control_object() -> None:
    provider = ScriptedS3()
    provider.seed_control()

    await adapter(provider).cancel_upload(storage_upload_id=SESSION_ID)

    assert provider.aborted and CONTROL_KEY not in provider.objects


@pytest.mark.parametrize(
    "object_key",
    ["../../etc/passwd", "tenant/../secret", "/leading-slash", "tenant/a\x00b", ""],
)
async def test_unusable_object_keys_never_reach_the_provider(object_key: str) -> None:
    provider = ScriptedS3()

    with pytest.raises(StoragePermanentError):
        await adapter(provider).get_object_metadata(object_key=object_key)

    assert provider.requests == []


async def test_unusable_session_identifiers_never_reach_the_provider() -> None:
    provider = ScriptedS3()

    with pytest.raises(StoragePermanentError):
        await adapter(provider).cancel_upload(storage_upload_id="../../control")

    assert provider.requests == []


async def test_persist_file_records_the_server_computed_digest(tmp_path: Path) -> None:
    provider = ScriptedS3()
    source = tmp_path / "derivative.wav"
    source.write_bytes(PAYLOAD)
    derivative_key = f"{OBJECT_KEY.rsplit('/', 2)[0]}/audio/source.wav"

    metadata = await adapter(provider).persist_file(
        object_key=derivative_key, source_path=source, content_type="audio/wav"
    )

    assert metadata.sha256_checksum == PAYLOAD_SHA256
    assert metadata.byte_size == len(PAYLOAD)
    stored_body, stored_headers = provider.objects[derivative_key]
    assert stored_body == PAYLOAD
    assert stored_headers["x-amz-meta-sha256"] == PAYLOAD_SHA256
    put = next(request for request in provider.requests if request.method == "PUT")
    # Content-Length must be explicit so the body is not chunk-encoded past the signature.
    assert put.headers["content-length"] == str(len(PAYLOAD))
    assert "transfer-encoding" not in put.headers


async def test_persist_file_reuses_an_already_stored_object(tmp_path: Path) -> None:
    provider = ScriptedS3()
    source = tmp_path / "derivative.wav"
    source.write_bytes(PAYLOAD)
    derivative_key = f"{OBJECT_KEY.rsplit('/', 2)[0]}/audio/source.wav"
    storage = adapter(provider)

    first = await storage.persist_file(
        object_key=derivative_key, source_path=source, content_type="audio/wav"
    )
    put_count = sum(1 for request in provider.requests if request.method == "PUT")
    second = await storage.persist_file(
        object_key=derivative_key, source_path=source, content_type="audio/wav"
    )

    assert first.sha256_checksum == second.sha256_checksum
    assert sum(1 for request in provider.requests if request.method == "PUT") == put_count


async def test_persist_file_refuses_an_empty_file(tmp_path: Path) -> None:
    provider = ScriptedS3()
    source = tmp_path / "empty.wav"
    source.write_bytes(b"")

    with pytest.raises(StoragePermanentError):
        await adapter(provider).persist_file(
            object_key=f"{OBJECT_KEY.rsplit('/', 2)[0]}/audio/source.wav",
            source_path=source,
            content_type="audio/wav",
        )


async def test_adapter_logs_never_carry_urls_signatures_or_keys() -> None:
    provider = ScriptedS3()
    provider.seed_control()
    storage = adapter(provider)

    with structlog.testing.capture_logs() as events:
        await storage.create_part_urls(
            storage_upload_id=SESSION_ID, expires_at=later(), part_numbers=(1, 2)
        )
        provider.status_overrides[f"HEAD {OBJECT_KEY}"] = 503
        with pytest.raises(StorageUnavailableError):
            await storage.get_object_metadata(object_key=OBJECT_KEY)

    assert events
    rendered = json.dumps(events)
    for forbidden in ("X-Amz-Signature", "X-Amz-Credential", "test-secret-key", OBJECT_KEY, "http"):
        assert forbidden not in rendered


async def test_virtual_host_addressing_moves_the_bucket_into_the_host() -> None:
    provider = ScriptedS3()

    instructions = await adapter(provider, s3_force_path_style=False).create_upload(
        storage_upload_id=SESSION_ID,
        object_key=OBJECT_KEY,
        content_type="video/mp4",
        expires_at=later(),
        part_numbers=(1,),
    )

    parsed = urlsplit(instructions[0].upload_url)
    assert parsed.netloc == "socialpilot-media.storage.public:9000"
    assert parsed.path == f"/{OBJECT_KEY}"


def test_configured_adapter_selection() -> None:
    assert isinstance(create_storage(settings()), S3MultipartStorage)
    assert isinstance(
        create_storage(settings(storage_adapter="fake", s3_bucket="")), FakeMultipartStorage
    )
