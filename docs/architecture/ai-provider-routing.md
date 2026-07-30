# AI Provider Routing for Media Analysis

## Goal

ASR and video understanding are capability contracts, not provider contracts. Phase 1 chooses a route only after the ingest gate, uses minimized proxy/scene inputs, validates normalized output, and attributes cost without exposing credentials or sensitive payloads.

## Ports and route decision

`SpeechToTextPort` returns normalized transcript segments. `VideoUnderstandingPort` returns schema-constrained scene facts such as summary, tags, classification, confidence, and approved quality signals. `AnalysisRoutePolicyPort` returns a persisted decision containing:

- capability and input class;
- provider/model alias and route-policy revision;
- timeout, maximum attempts, permitted fallback, data-region requirement, and concurrency limit;
- approved estimated maximum cost in integer minor units and currency.

The application service persists the decision before a paid/provider call. An adapter receives only the data allowed by that decision, performs its own timeout/redaction/error translation, and returns a neutral result. Provider SDK objects, secret names, signed URLs, raw request/response payloads, and provider-specific status codes do not escape the adapter.

## Input and output safety

- Use a controlled short-lived worker reference to a verified proxy or approved scene; never fetch a client URL or pass an original by default.
- Media-embedded text, ASR text, detected signage, and provider output are untrusted data, not instructions.
- Validate JSON schema, bounds, enums, text/array sizes, confidence range, scene IDs, and semantic constraints before persistence.
- Reject malformed/unsafe outputs with a stable code. Never execute provider-suggested URLs, tool calls, or configuration.

## Fallback and cost

Fallback occurs only for classified transient/throttled provider failure, within the persisted route policy, region/privacy constraint, retry budget, and approved cost cap. Policy, validation, budget, and invalid-response failures do not fall through to another provider automatically. **`provider_usage` is a planned table, not an implemented one (2026-07-30).** No migration or model creates it yet; W08 surfaced this gap after this document already described it in the present tense. The intended record carries tenant/job/asset/run/capability/provider/model, estimated and actual integer-minor-unit cost, currency, duration, outcome, and correlation ID, and deliberately excludes token values, prompts, signed URLs, and full responses. The benchmark harness produces a `ProviderUsageRecord` value with exactly this shape so the table can back it without reshaping call sites; creating the table is scheduled on the next migration slot (W04). Until then, cost attribution is per-run output only and is **not** durable.

## Operations

Provider health, latency, error class, throttle state, queue delay, attempted/final cost, and success rate are observability data. Route changes and production-provider enablement require configuration/operations approval and contract tests; no provider is hard-coded into domain entities or controllers.
