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

Fallback occurs only for classified transient/throttled provider failure, within the persisted route policy, region/privacy constraint, retry budget, and approved cost cap. Policy, validation, budget, and invalid-response failures do not fall through to another provider automatically. `provider_usage` records tenant/job/asset/run/capability/provider/model, estimated and actual integer-minor-unit cost, currency, duration, outcome, and correlation ID. It deliberately excludes token values, prompts, signed URLs, and full responses.

## Operations

Provider health, latency, error class, throttle state, queue delay, attempted/final cost, and success rate are observability data. Route changes and production-provider enablement require configuration/operations approval and contract tests; no provider is hard-coded into domain entities or controllers.

## Benchmark harness (W08)

Before any real provider is wired, it is measured. The harness (`app/benchmark/`, run with
`make benchmark`) is an **offline measurement tool, not a domain module**: it selects no
provider and persists nothing. It runs a fixed golden set (PRD §40.5) through a provider set —
deterministic **fake** providers by default, so it works in CI with no credentials and no
database — and scores output against machine-readable ground truth committed under
`services/api/tests/fixtures/golden/`.

- **One metric per capability, against ground truth.** ASR (WER, timestamp drift, noisy
  degradation, brand-term hit), video understanding (scene-label Jaccard, object F1,
  unsafe-flag accuracy, and schema fidelity measured with the *real* `normalize_provider_output`
  validator), text strategy (forbidden-word and fabricated price/date counts — both must be
  zero — and approved-CTA rate; brand tone is reported as **not auto-scored**), structured
  timeline (strict conformance + boundary degradation), TTS (segment-duration deviation,
  Turkish-phoneme coverage; prosody is **not auto-scored**).
- **Cost and latency use one record shape.** Each call produces a neutral `ProviderUsageRecord`
  mirroring the ADR-007 `provider_usage` fields (capability/provider/model, estimated + actual
  integer-minor cost, currency, duration, outcome, correlation id) plus route revision and
  prompt version. There is **no parallel cost model**. Note: a persisted `provider_usage` table
  does not yet exist in code; this record is the shape a future persistence layer adopts.
- **Cost cap halts before spending.** `CostLedger.reserve` checks the estimated cost of the
  next call against the cap and stops the run without invoking the provider when it would be
  breached — it never silently spends past the ceiling.
- **Provenance is mandatory.** A sample without a prompt version or route revision is refused;
  an unattributable measurement is never emitted (§17.6).
- **Data minimization.** Providers receive a proxy/scene reference, never the original (§34.3).
- **Region and legal eligibility are first-class output columns.** The comparison table shows
  each provider's data region and whether it may lawfully receive face/voice-bearing input
  under KVKK cross-border rules; the best score is not the winner if the input cannot lawfully
  be sent.
- **Non-determinism.** `--runs N` re-invokes each provider (paying each time) and the report
  carries the min/max/mean/stdev distribution.

The harness selects nothing. The PM reads its output and records the provider decision as an
ADR.
