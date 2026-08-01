# W15/W16 adversarial verification

## Scope

- Tester-only verification at merged `main`; production code and committed tests are out of scope.
- Attack the W15 HTTP/TTS boundaries, measured-duration source of truth, storage logging, and
  idempotency.
- Re-attack W16 normalization and logging hooks, excluding the documented W17 grammar gaps and
  deliberately bypassed custom `Handler.handle` implementation.

## Expected documentation changes

- `docs/handoffs/W15-tts-voiceover.md`
- `docs/handoffs/W16-verification-followups-3.md`

## Verification

1. Run isolated compose as `COMPOSE_PROJECT_NAME=sp-codex` and record runtime versions.
2. Perform reproducible API/process probes plus focused existing suites without writing features.
3. Append findings and delivery decision to both work orders, then archive this plan.
