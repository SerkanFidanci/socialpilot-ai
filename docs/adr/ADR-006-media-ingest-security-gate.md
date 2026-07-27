# ADR-006: Media Ingest Security Gate

**Status:** Accepted
**Date:** 2026-07-28

## Context

Phase 0 accepts a direct-to-storage multipart upload and creates a durable `media.ingest` job, but it intentionally does not claim that the uploaded bytes are safe or technically valid. Client MIME, filename, declared checksum, and embedded metadata are attacker-controlled. Allowing FFprobe, FFmpeg, ASR, VLM, or downstream content work before validation would expose workers/providers to malformed, malicious, cross-tenant, or expensive input.

## Decision

Phase 1 places an explicit ingest security gate before all derivative and AI analysis work. A tenant-scoped worker validates immutable storage metadata and SHA-256, identifies actual content/container, applies policy limits, and calls a provider-neutral malware-scanning port. Only clean, policy-compliant assets transition from `validating` to `processing`. Unsupported/corrupt/checksum-invalid media is rejected; infected or security-indeterminate media is quarantined. Scanner unavailability may retry but never becomes an implicit pass.

FFprobe/FFmpeg/parser execution runs in an isolated least-privilege worker with fixed executable paths and argument arrays, restricted temporary directories, time/resource/output limits, cleanup, and no user-composed shell string. The API and n8n never proxy media bytes.

## Consequences

- `ready` means the asset passed server-side gate and required analysis, not merely that upload completion succeeded.
- Additional inspection/scan models, error codes, worker controls, tests, and operational handling are required.
- Original/provider credentials/signed URLs and raw tool/scan output stay outside public errors, logs, and audit payloads.
- A production scanner may be added behind the port without changing domain state rules.

## Rejected alternatives

- Trusting client MIME/extension or checksum alone: rejected because these can be forged.
- Scanning asynchronously after proxy/AI analysis: rejected because unsafe bytes would already cross processing boundaries.
- Proxying uploads through FastAPI for inspection: rejected because it violates the direct-upload boundary and creates unnecessary media-data-path risk.
