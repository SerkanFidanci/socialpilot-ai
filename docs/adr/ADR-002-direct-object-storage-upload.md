# ADR-002: Direct Object-Storage Upload

**Status:** Accepted for Phase 0 planning
**Date:** 2026-07-27

## Context

Mobile clients upload large, resumable media. Proxying it through FastAPI adds unnecessary bandwidth, latency, cost, failure modes, and capacity risk. n8n is unsuitable for binary transfer and cannot be the media-data path.

## Decision

The API creates an authorized, tenant-scoped multipart upload session through a provider-neutral storage port. The client uploads parts directly to object storage using short-lived, least-privilege instructions, then calls a completion endpoint. The API verifies object metadata/checksum through the port and atomically creates the media asset, ingest job, and outbox event.

Slice 0C may implement this port with a local fake or MinIO-compatible adapter. Selecting, credentialing, provisioning, or connecting a production object-storage provider is explicitly outside Slice 0C.

## Consequences

- FastAPI and n8n never proxy original media bytes.
- Provider-specific URLs, object types, and credentials are confined to a storage adapter.
- Upload completion is a security boundary requiring membership authorization, session state validation, checksum/metadata verification, and idempotency.
- Actual content inspection, malware detection, ffprobe validation, and proxy generation remain later worker work; client MIME alone is insufficient.

## Rejected alternatives

- API multipart relay: rejected for scalability and reliability reasons.
- n8n binary workflows: rejected because n8n is orchestration-only and not a media transport.
- Direct unrestricted client credentials: rejected because they cannot enforce narrow tenant/object/session scope.
