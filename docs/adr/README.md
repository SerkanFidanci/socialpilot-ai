# Architecture Decision Record Catalog

## Numbering rule

The actual ADR filenames in this directory are the sole source of truth for ADR identifiers. The identifier is the zero-padded numeric prefix in a filename such as `ADR-005-transactional-outbox.md`. Product requirement lists or planning notes may propose decisions, but they are not the ADR catalog.

## Current ADRs

| ADR | Decision |
|---|---|
| [ADR-001](ADR-001-modular-monolith.md) | Modular Monolith |
| [ADR-002](ADR-002-direct-object-storage-upload.md) | Direct Object-Storage Upload |
| [ADR-003](ADR-003-n8n-orchestration-boundary.md) | n8n Orchestration Boundary |
| [ADR-004](ADR-004-provider-adapter-pattern.md) | Provider Adapter Pattern |
| [ADR-005](ADR-005-transactional-outbox.md) | Transactional Outbox |

## Creating a new ADR

1. Inspect this directory and select the next unused sequential numeric identifier.
2. Create `ADR-NNN-short-kebab-case-title.md` using the sections **Status**, **Date**, **Context**, **Decision**, **Consequences**, and **Rejected alternatives**.
3. Add the document to `docs/index.md` and this catalog in the same change.
4. Record whether the ADR is proposed, accepted, superseded, or deprecated; link any ADR it supersedes rather than editing history away.

## Immutability rule

Accepted ADRs are not renumbered. If a decision changes, add a new ADR that supersedes or amends the earlier ADR and keep the original filename and history intact.
