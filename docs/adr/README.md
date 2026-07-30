# Architecture Decision Record Catalog

## Numbering rule

The actual ADR filenames in this directory are the sole source of truth for ADR identifiers. The identifier is the zero-padded numeric prefix in a filename such as `ADR-005-transactional-outbox.md`. Product requirement lists or planning notes may propose decisions, but they are not the ADR catalog.

In particular, the list in PRD §47 (now
[98-risks.md](../product/requirements/98-risks.md)) is a **dated proposal** written before
implementation. Its numbering does not match this directory and must never be used to
resolve an ADR identifier.

## Current ADRs

| ADR | Decision | Status | Date |
|---|---|---|---|
| [ADR-001](ADR-001-modular-monolith.md) | Modular Monolith | Accepted for Phase 0 planning | 2026-07-27 |
| [ADR-002](ADR-002-direct-object-storage-upload.md) | Direct Object-Storage Upload | Accepted for Phase 0 planning | 2026-07-27 |
| [ADR-003](ADR-003-n8n-orchestration-boundary.md) | n8n Orchestration Boundary | Accepted for Phase 0 planning | 2026-07-27 |
| [ADR-004](ADR-004-provider-adapter-pattern.md) | Provider Adapter Pattern | Accepted for Phase 0 planning | 2026-07-27 |
| [ADR-005](ADR-005-transactional-outbox.md) | Transactional Outbox | Accepted for Phase 0 planning | 2026-07-27 |
| [ADR-006](ADR-006-media-ingest-security-gate.md) | Media Ingest Security Gate | Accepted | 2026-07-28 |
| [ADR-007](ADR-007-media-analysis-provider-routing.md) | Media Analysis Provider Routing | Accepted | 2026-07-28 |
| [ADR-008](ADR-008-s3-compatible-storage-adapter.md) | S3-Compatible Storage Adapter | Accepted | 2026-07-30 |
| [ADR-009](ADR-009-dependency-and-runtime-baseline.md) | Dependency and Runtime Baseline | Accepted | 2026-07-30 |
| [ADR-010](ADR-010-valkey-runtime-evaluation.md) | Valkey Runtime Evaluation | Proposed | 2026-07-30 |
| [ADR-011](ADR-011-real-media-materializer.md) | Real Media Materializer and `.mov`/HEVC Analysis Gate | Accepted | 2026-07-30 |
| [ADR-012](ADR-012-remove-n8n-from-mvp.md) | n8n'i MVP Kapsamından Çıkarma (ADR-003'ün yerini alır) | Accepted | 2026-07-30 |
| [ADR-013](ADR-013-single-server-deployment-topology.md) | Tek Sunucu Dağıtım Topolojisi | Accepted | 2026-07-30 |
| [ADR-014](ADR-014-opentelemetry-observability-foundation.md) | OpenTelemetry Observability Foundation | Accepted | 2026-07-30 |
| [ADR-015](ADR-015-parametric-editing-model.md) | Parametrik Düzenleme Veri Modeli | Accepted | 2026-07-30 |
| [ADR-016](ADR-016-render-port.md) | RenderPort ve Render Adapter Sınırı | Accepted | 2026-07-30 |

Paralel work order'lar bu katalogu ve router'i degistirmeden daha yuksek numarali ADR
dosyalari ekleyebilir; merge eden bu dizini tarar ve satirlari merge commit'inde ekler. Var
olmayan bir ADR icin asla satir yazilmaz. **Numara cakismasi gercek bir risktir:** 2026-07-30'da
W02 ve W09 paralel calisirken ikisi de ADR-009'u aldi; W09'un dosyasi merge sirasinda ADR-011'e
tasindi (henuz katalogda olmadigi icin yeniden numaralandirma mesruydu). Kabul edilmis ve
kataloga girmis bir ADR asla yeniden numaralandirilmaz.

## Creating a new ADR

1. Inspect this directory and select the next unused sequential numeric identifier.
2. Create `ADR-NNN-short-kebab-case-title.md` using the sections **Status**, **Date**, **Context**, **Decision**, **Consequences**, and **Rejected alternatives**.
3. Add the document to `docs/index.md` and this catalog in the same change.
4. Record whether the ADR is proposed, accepted, superseded, or deprecated; link any ADR it supersedes rather than editing history away.

## Immutability rule

Accepted ADRs are not renumbered. If a decision changes, add a new ADR that supersedes or amends the earlier ADR and keep the original filename and history intact.
