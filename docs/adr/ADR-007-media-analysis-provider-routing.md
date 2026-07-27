# ADR-007: Media Analysis Provider Routing

**Status:** Accepted
**Date:** 2026-07-28

## Context

Phase 1 needs ASR and video-understanding capability, but providers differ by language quality, privacy region, latency, cost, availability, and response schema. ADR-004 requires provider adapters generally, but it does not define how an analysis request is selected, cost-bounded, replayed, or normalized. Hard-coding provider/model selection in controllers or entities would leak SDK concepts and make safe fallback impossible.

## Decision

Use capability-focused `SpeechToTextPort` and `VideoUnderstandingPort` behind an application-level analysis route policy. Before a provider call, persist a route snapshot with capability, provider/model alias, route revision, timeout, permitted fallback, regional/privacy requirement, and maximum approved integer-minor-unit cost. Adapters receive only approved verified proxy/scene inputs, own timeout/redaction/error translation, and return schema-validated neutral values. Persist normalized results and attributable provider usage, not provider SDK types or full raw payloads.

Fallback is permitted only for classified transient/throttled failure when policy, privacy, retry, and cumulative cost constraints all allow it. Production provider onboarding is configuration/operations work and is not selected by this ADR.

## Consequences

- Results have reproducible route provenance and cost attribution per tenant/job/run.
- Provider adapters need contract fixtures, strict response validation, and safe error classes.
- Route policy and usage records introduce schema/operational complexity, accepted to prevent lock-in, unbounded cost, and opaque retries.
- Tokens, signed URLs, raw prompts/responses, and provider status payloads remain out of logs/audit/public responses.

## Rejected alternatives

- One hard-coded ASR/VLM provider: rejected for lock-in, regional/privacy risk, and brittle availability/cost behaviour.
- Letting each worker choose any fallback at runtime: rejected because it bypasses deterministic budget/privacy policy.
- Saving raw provider payloads by default for debugging: rejected because they can contain sensitive media-derived data and create retention risk.
