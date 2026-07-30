# ADR-008: S3-Compatible Object-Storage Adapter

**Status:** Accepted for Phase 1E
**Date:** 2026-07-30
**Supersedes nothing. Implements the adapter boundary left open by [ADR-002](ADR-002-direct-object-storage-upload.md).**

## Context

ADR-002 established a provider-neutral `MultipartStoragePort` and allowed Slice 0C to satisfy
it with a local fake. That fake accepts no bytes: it hands out part URLs on an intentionally
unreachable host and can only be completed through an in-process test hook. The direct-upload
byte path therefore never ran, the mobile client's third step could not work, and the Phase 1
exit criterion could not be closed.

The adapter has to serve MinIO in development and S3 or R2 in production without leaking
provider types past its own module, and it must verify completion without trusting anything
the client declares.

## Decision

Implement `S3MultipartStorage` against the S3 REST API, selected by configuration
(`STORAGE_ADAPTER=fake|s3`). The byte-free fake stays the default for development and the
control-plane test suite; `production` refuses it at settings validation, matching the
existing identity-adapter guard.

### SigV4 over `httpx`, not a vendor SDK

The adapter signs requests itself with `hmac`/`hashlib` and issues them through `httpx`.
`boto3` and the MinIO SDK are synchronous, so they would need a thread pool in an async
request path, and neither ships type information that satisfies `mypy --strict` without
adding stub dependencies and an ignore rule. `httpx` was already present, is fully typed, and
makes the per-request timeout the engineering rules demand trivial to set. The cost is that
this repository owns its SigV4 implementation; it is covered by adapter contract tests and by
an end-to-end test against real MinIO.

### Completion is verified against storage, never against the request

Completion runs `ListParts` → `CompleteMultipartUpload` → `HeadObject` → streamed `GetObject`:

1. `ListParts` yields the provider's own part inventory. The client's declared part numbers
   and ETags are compared against it, and the finalize request is built from the observed
   inventory, so a forged ETag cannot be finalized.
2. `HeadObject` yields the authoritative size, content type, and ETag.
3. The object is streamed once, in 1 MiB chunks, to observe its SHA-256 server-side.

The declared content type is stamped on the object at `CreateMultipartUpload`, which is why
`MultipartStoragePort.create_upload` now takes `content_type`: completion compares the stored
content type against the asset instead of re-reading the client's claim.

**The streamed digest is a deliberate trade-off.** A full-object read at completion costs
bandwidth and latency proportional to `MEDIA_MAX_BYTES`. The alternative — asking the provider
for a full-object SHA-256 via `ChecksumAlgorithm=SHA256` with `ChecksumType=FULL_OBJECT` — is
free, but support varies across S3-compatible providers and MinIO releases, and a fallback path
would be the only correctness guarantee while remaining almost untested. One portable path
that is always correct beats two paths where the important one rarely runs. Objects written by
the adapter itself (`persist_file`) carry a server-computed `x-amz-meta-sha256`, so worker
derivatives are verified from metadata with no re-read. Bytes never traverse FastAPI on the
client's behalf: this is a server-to-storage read, not a relay.

### Multipart state lives in a control object, not in the database

The port identifies an upload by the server-generated `storage_upload_id` persisted in
`media_upload_sessions.storage_upload_id`, declared `String(128)`. A provider `UploadId` does
not fit: AWS values routinely exceed 128 characters, and widening the column requires a
migration that this slice does not hold a slot for.

`create_upload` therefore writes a small server-owned JSON object at
`_control/uploads/{storage_upload_id}.json` holding the tenant object key and the provider
upload id; the part-URL, completion, and cancellation calls resolve through it, and it is
deleted when the upload finalizes or is cancelled. If the control write fails, the provider-side
multipart upload is aborted so no orphan is left behind.

This keeps the adapter stateless across processes — the API and the Celery workers hold
independent instances against the same bucket — at the price of one small object per in-flight
upload and one extra request per operation.

### Presigning targets the address the client will contact

SigV4 binds the signature to the `Host` header, so a URL signed for a Compose service name is
unusable from a phone. `S3_ENDPOINT_URL` is the server-side endpoint; `S3_PRESIGN_ENDPOINT_URL`
is the client-reachable one and defaults to the former. Part URLs are signed for the shortest
of the remaining session lifetime and `S3_PRESIGN_TTL_SECONDS`, and an expired session is
refused before any signature is produced.

### Errors stay neutral

The adapter raises only `StorageUnavailableError` (throttling, gateway churn, outage, credential
rejection) and `StoragePermanentError` (a provider answer that contradicts what the system
asked for). Provider response bodies, URLs, signatures, and credentials never enter an
exception message, a log event, an audit row, or a Problem Details body. In `complete`, a
permanent adapter error surfaces as the existing `UPLOAD_CHECKSUM_MISMATCH` (409) — the object
or its parts cannot be verified — so no new error code enters the catalogue.

## Consequences

- The direct-upload byte path works against a real provider; blocker **B1** is resolved.
- `image/heic`, `image/heif`, and `video/quicktime` are admitted at the upload boundary, so the
  iOS main flow is no longer rejected on arrival. Admission is not analysis: only `video/mp4`
  currently enters the technical pipeline, so `.mov` and HEIC assets stop after ingest. Closing
  that gap is a separate slice.
- Completion cost grows with object size. If it becomes material, the upgrade path is
  provider-reported full-object checksums, with the streamed read kept as the fallback.
- One `httpx.AsyncClient` is created per storage operation. Storage calls are per-upload rather
  than per-byte, so this is acceptable; a pooled client owned by the application lifespan is the
  natural next step and needs shutdown plumbing in both composition roots.
- The `_control/` prefix is a server-owned namespace inside the media bucket. When a migration
  slot is available, widening `storage_upload_id` and storing the provider `UploadId` directly
  removes it.
- The worker still materializes media through the fixture-backed `FakeMediaMaterializer`, so
  stages after ingest do not read real storage bytes yet. A real materializer is required before
  the Phase 1 exit criterion can be demonstrated end to end.

## Rejected alternatives

- **`boto3`/`aioboto3`:** synchronous client in an async path, or tight `botocore` pinning, plus
  type-stub dependencies and a `mypy` ignore rule for a single adapter.
- **Trusting the client's declared checksum and part ETags:** the completion endpoint is a
  security boundary; a declaration is an assertion, never proof.
- **Persisting the provider `UploadId` in the existing column:** silently truncates real AWS
  identifiers.
- **Resolving multipart state with `ListMultipartUploads`:** needs a bucket-wide list permission
  and threads the object key through every port method, for the same result as one small
  control object.
- **Letting the adapter create buckets:** provisioning is an operations concern, deliberately
  outside the adapter (Compose runs a one-shot `mc mb` for development only).
