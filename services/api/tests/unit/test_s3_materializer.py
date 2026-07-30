"""Contract tests for the streaming media materializer against a scripted provider.

These run without network or credentials. The real-provider proof lives in
`tests/integration/test_real_media_pipeline.py`.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest
import structlog
from pydantic import SecretStr

from app.core.config import Settings
from app.infrastructure.media import create_materializer
from app.infrastructure.media.fake_ingest import FakeMediaMaterializer
from app.infrastructure.media.s3_materializer import S3MediaMaterializer, _destination_name
from app.infrastructure.storage.s3 import S3MultipartStorage
from app.modules.media.storage import StoragePermanentError, StorageUnavailableError

OBJECT_KEY = "tenant/11111111-1111-1111-1111-111111111111/media/asset/original/source.mp4"
SECRET = "test-secret-key"


def settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_url": "postgresql+asyncpg://user:password@localhost:5432/socialpilot",
        "redis_url": "redis://localhost:6379/0",
        "celery_broker_url": "redis://localhost:6379/1",
        "celery_result_backend": "redis://localhost:6379/2",
        "storage_adapter": "s3",
        "materializer_adapter": "s3",
        "s3_endpoint_url": "http://storage.internal:9000",
        "s3_bucket": "socialpilot-media",
        "s3_access_key_id": SecretStr("test-access-key"),
        "s3_secret_access_key": SecretStr(SECRET),
    }
    values.update(overrides)
    return Settings.model_validate(values)


class MockObjectStore:
    """A minimal S3 stand-in serving HEAD/GET for stored objects."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.status_overrides: dict[str, int] = {}
        self.reported_size: int | None = None
        self.requests: list[httpx.Request] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        key = urlsplit(str(request.url)).path.removeprefix("/socialpilot-media/")
        override = self.status_overrides.get(f"{request.method} {key}")
        if override is not None:
            return httpx.Response(override)
        body = self.objects.get(key)
        if body is None:
            return httpx.Response(404)
        size = self.reported_size if self.reported_size is not None else len(body)
        headers = {"content-length": str(size), "content-type": "video/mp4", "etag": '"e"'}
        if request.method == "HEAD":
            return httpx.Response(200, headers=headers)
        return httpx.Response(200, content=body, headers=headers)


def materializer(store: MockObjectStore, **overrides: object) -> S3MediaMaterializer:
    resolved = settings(**overrides)
    storage = S3MultipartStorage(resolved, transport=store.transport())
    return S3MediaMaterializer(resolved, storage=storage)


async def test_materialize_streams_object_to_workdir(tmp_path: Path) -> None:
    payload = b"real-media-bytes" * 100_000  # spans many 1 MiB stream chunks
    store = MockObjectStore()
    store.objects[OBJECT_KEY] = payload

    path = await materializer(store).materialize(object_key=OBJECT_KEY, workdir=tmp_path)

    assert path.is_file()
    assert path.read_bytes() == payload
    assert hashlib.sha256(path.read_bytes()).hexdigest() == hashlib.sha256(payload).hexdigest()
    # Size is checked with HEAD before the body is streamed with GET.
    assert [request.method for request in store.requests][:2] == ["HEAD", "GET"]


async def test_missing_object_is_permanent_and_leaves_no_file(tmp_path: Path) -> None:
    store = MockObjectStore()

    with pytest.raises(StoragePermanentError):
        await materializer(store).materialize(object_key=OBJECT_KEY, workdir=tmp_path)

    assert list(tmp_path.iterdir()) == []


async def test_transient_outage_leaves_no_partial_file(tmp_path: Path) -> None:
    store = MockObjectStore()
    store.objects[OBJECT_KEY] = b"data"
    store.status_overrides[f"GET {OBJECT_KEY}"] = 503

    with pytest.raises(StorageUnavailableError):
        await materializer(store).materialize(object_key=OBJECT_KEY, workdir=tmp_path)

    # No partial file survives the failure (PRD §19.3).
    assert list(tmp_path.iterdir()) == []


async def test_oversize_object_is_refused_before_the_body_is_pulled(tmp_path: Path) -> None:
    store = MockObjectStore()
    store.objects[OBJECT_KEY] = b"small"
    ceiling = max(
        settings().media_max_bytes,
        settings().media_max_derivative_bytes,
        settings().media_max_extracted_audio_bytes,
    )
    store.reported_size = ceiling + 1

    with pytest.raises(StoragePermanentError):
        await materializer(store).materialize(object_key=OBJECT_KEY, workdir=tmp_path)

    # Refused on the HEAD; the body is never streamed and no file is created.
    assert all(request.method != "GET" for request in store.requests)
    assert list(tmp_path.iterdir()) == []


async def test_size_disagreement_during_download_is_permanent(tmp_path: Path) -> None:
    store = MockObjectStore()
    store.objects[OBJECT_KEY] = b"the-actual-bytes"
    store.reported_size = len(b"the-actual-bytes") - 1  # HEAD lies smaller than the body

    with pytest.raises(StoragePermanentError):
        await materializer(store).materialize(object_key=OBJECT_KEY, workdir=tmp_path)

    assert list(tmp_path.iterdir()) == []


async def test_unusable_object_key_never_reaches_the_provider(tmp_path: Path) -> None:
    store = MockObjectStore()

    with pytest.raises(StoragePermanentError):
        await materializer(store).materialize(object_key="../../etc/passwd", workdir=tmp_path)

    assert store.requests == []
    assert list(tmp_path.iterdir()) == []


async def test_materializer_never_logs_urls_signatures_or_keys(tmp_path: Path) -> None:
    store = MockObjectStore()
    store.objects[OBJECT_KEY] = b"data"
    store.status_overrides[f"GET {OBJECT_KEY}"] = 503

    with structlog.testing.capture_logs() as events:
        with pytest.raises(StorageUnavailableError) as raised:
            await materializer(store).materialize(object_key=OBJECT_KEY, workdir=tmp_path)

    rendered = json.dumps(events) + str(raised.value)
    for forbidden in ("X-Amz-Signature", "X-Amz-Credential", SECRET, OBJECT_KEY, "http"):
        assert forbidden not in rendered


@pytest.mark.parametrize(
    ("object_key", "expected"),
    [
        ("tenant/x/media/a/original/source.mp4", "materialized.mp4"),
        ("tenant/x/media/a/original/clip.mov", "materialized.mov"),
        ("tenant/x/media/a/derivatives/proxy", "materialized"),
        ("tenant/x/media/a/original/name.with.dots.MP4", "materialized.mp4"),
        ("tenant/x/media/a/original/weird.<script>", "materialized"),
    ],
)
def test_destination_name_only_copies_a_safe_suffix(object_key: str, expected: str) -> None:
    assert _destination_name(object_key) == expected


def test_create_materializer_selects_by_configuration() -> None:
    assert isinstance(create_materializer(settings()), S3MediaMaterializer)
    fake = settings(
        storage_adapter="fake",
        materializer_adapter="fake",
        s3_endpoint_url="",
        s3_bucket="",
        s3_access_key_id=SecretStr(""),
        s3_secret_access_key=SecretStr(""),
    )
    assert isinstance(create_materializer(fake), FakeMediaMaterializer)
