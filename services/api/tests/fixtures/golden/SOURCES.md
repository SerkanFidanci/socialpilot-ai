# Golden media set — sources and licenses

This directory is the fixed benchmark input for PRD §40.5. It is **machine-readable ground
truth**, not media bytes: no large binary is committed. `scripts/make_golden_media.py`
regenerates every FFmpeg-producible sample deterministically from the `spec` block in each
`samples/<id>.json`; nothing here is downloaded at run time.

## What is committed

- `samples/<id>.json` — one sample. Carries its qualities, an FFmpeg generation `spec` (or an
  `operator_supplied` reference), and, per capability, a machine-readable `ground_truth` plus a
  `fake_output` (what the default deterministic provider returns, deliberately imperfect so the
  metrics compute non-trivial numbers and the harness self-test can assert exact values).
- This `SOURCES.md`.

## What is NOT committed and why

- **No media bytes.** Synthetic clips are regenerated on demand into a scratch directory; real
  video/audio is never committed (repo hygiene + the DoD rule against large binaries).
- **No real Turkish speech audio.** Deterministic FFmpeg cannot synthesize natural Turkish
  speech. The default benchmark run uses **fake adapters** that ignore the audio bytes entirely,
  so a real recording is only needed when a *real* ASR/TTS provider is wired for a credentialed
  run. For that case, supply an open-licensed clip from a listed source below and record it in
  the sample's `media.operator_supplied` block; the generator writes a silent, correctly-timed
  stand-in track otherwise so the pipeline shape is still exercised.

## Approved open-license sources for operator-supplied Turkish speech

Use only clips whose license permits redistribution and derivative use, and record the exact
source URL + license in the sample file before a real-provider run:

| Source | License | Note |
|---|---|---|
| Mozilla Common Voice — Turkish | CC0-1.0 | Public-domain dedication; short validated clips. |
| Wikimedia Commons — Turkish spoken audio | CC-BY-SA / CC0 (per file) | Verify each file's own license. |
| Own recording with written consent | operator-owned | KVKK: voice is biometric-adjacent; keep the consent record. |

> A clip carrying a face or a voice is personal (and arguably biometric) data. Under KVKK
> cross-border transfer rules (see
> [99-external-platform-facts.md](../../../../../docs/product/requirements/99-external-platform-facts.md)),
> sending it to a foreign provider needs a standard contract + Board notification. The benchmark
> report's **data region** and **face/voice eligible** columns exist so an ineligible provider
> cannot win on score alone.
