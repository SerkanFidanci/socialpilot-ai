# ADR-004: Provider Adapter Pattern

**Status:** Accepted for Phase 0 planning
**Date:** 2026-07-27

## Context

Identity, object storage, AI, social, advertising, billing, and notification providers can change by capability, geography, cost, availability, or policy. Domain rules must not depend on any provider SDK, credential format, or response shape.

## Decision

Define capability-focused ports at application/domain boundaries and implement provider-specific adapters in infrastructure. Slice 0B establishes the identity-verification port and test adapter; Slice 0C establishes the multipart-storage port and may use a local fake or MinIO-compatible adapter. Real identity-provider integration follows Slice 0B and production storage connection follows Slice 0C. Future providers are selected through configuration and explicit routing/policy layers, not hard-coded in controllers or entities.

## Consequences

- Tests use fakes/contract fixtures without production credentials or network calls.
- Adapters own timeouts, transient-error translation, response validation, redaction, and provider telemetry.
- Domain APIs use neutral value objects and error classes; provider SDK types cannot cross the boundary.
- The pattern adds interfaces and contract tests, but avoids lock-in and permits safe fallback/routing later.

## Rejected alternatives

- Calling provider SDKs from controllers: rejected because it mixes transport, domain rules, retries, secrets, and provider coupling.
- A generic untyped `provider.call()` abstraction: rejected because capability-specific contracts and validation are needed for safe behavior.
