@AGENTS.md

# Claude Code Specific Instructions

## Reading protocol

- Read [`docs/STATUS.md`](docs/STATUS.md) first, then find your task-type row in the router
  [`docs/index.md`](docs/index.md) and read **only** what that row lists. Do not read the
  documentation set breadth-first.
- The `CLAUDE.md` of a directory you work in loads automatically. Trust it: it lists the
  module's invariants, one line per file, and the test paths. Grep after reading it — do not
  discover a module with wide `Read` calls.
- Never read `docs/generated/openapi.json`, `docs/product/product-requirements.md`, or
  anything under `docs/plans/completed/` in full. See the context budget rules in
  [AGENTS.md](AGENTS.md).

## Working rules

- Use plan mode before changes spanning multiple modules.
- Run `/context` at the beginning of a new repository session when instruction loading is
  uncertain.
- When a work order in [`docs/handoffs/`](docs/handoffs/README.md) drives the session, that
  work order's file list is binding: touching a file you do not own means stopping and
  reporting, not deciding.
- When a module's files change, update that module's `CLAUDE.md` in the same change.
- Verify a dependency version against the package registry before writing it; never from
  memory. External platform versions, prices and limits come from
  [`99-external-platform-facts.md`](docs/product/requirements/99-external-platform-facts.md).

## Memory

- Use auto memory only for local debugging discoveries and workflow preferences.
- Do not treat auto memory as the source of truth for architecture, billing, security or
  advertising rules.
