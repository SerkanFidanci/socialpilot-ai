# ADR-010: Valkey as the Redis-Compatible Runtime

**Status:** Proposed (recommendation only — not implemented in this slice)
**Date:** 2026-07-30
**Relates to [ADR-009](ADR-009-dependency-and-runtime-baseline.md). Implementation, if
accepted, belongs to W06 (PostgreSQL 18 + runtime images), which owns `compose.yaml`.**

> This ADR records a recommendation for a later decision. It changes no runtime here: W02
> deliberately does not touch `compose.yaml` (owned by W01) and does not migrate the broker or
> cache image. The Python client is unchanged.

## Context

The system uses a Redis-compatible server in two roles: the Celery broker/result backend and
the application cache/readiness dependency. Development runs `redis:7-alpine`; the Python client
is `redis-py` (`redis>=5.2`, resolved to 8.1.0 in `uv.lock`).

Redis's licensing has moved repeatedly. Per the verified external-platform facts
([99-external-platform-facts.md](../product/requirements/99-external-platform-facts.md)),
current **Redis 8 is AGPL** and **Valkey 9.1 is BSD**. Valkey is the Linux Foundation's
BSD-3-licensed fork of Redis 7.2, wire-compatible with the Redis protocol and RESP, and a drop-in
for the broker and cache roles this system uses. AGPL's network-copyleft clause is a live
question for a hosted SaaS even when the server is only operated, not modified — exactly the
single-server topology decision K5 commits to.

This is a runtime/licensing choice, not a code change: the application talks RESP through
`redis-py` and Celery/kombu, none of which depend on the server being Redis rather than Valkey.

## Decision (proposed)

When W06 migrates the runtime images, **adopt Valkey in place of Redis** for both the broker and
the cache, pinned to a specific Valkey image digest. Keep `redis-py` as the client — it speaks to
Valkey unchanged. Verify at implementation time, against the registry and the facts file (a facts
line older than six months is untrusted):

1. The exact current Valkey version and its BSD-3 license.
2. Wire compatibility with the `redis-py` and `kombu`/Celery versions frozen in `uv.lock`.
3. That the Compose healthcheck (`redis-cli ping` / `valkey-cli ping`) and readiness probe still
   hold.

If any check fails, stay on a BSD-era Redis line and re-open this ADR.

## Consequences (if accepted)

- Removes the AGPL question from the broker and cache without touching application code.
- One more concern off W06's plate is licensing; the mechanical change is the image reference and
  the CLI name in the healthcheck.
- The client library, task code, and readiness logic are unaffected, so the blast radius is the
  Compose service definition and its healthcheck.

## Rejected alternatives (for the eventual decision)

- **Stay on Redis 8 (AGPL):** viable only after a deliberate legal read of AGPL against a hosted,
  unmodified-server SaaS. Defers a licensing question rather than closing it.
- **Pin an older BSD-licensed Redis and never upgrade:** forgoes security patches on the server to
  avoid the license move — trades one risk for a worse one.
- **Swap the client library too:** unnecessary; `redis-py` is wire-compatible with Valkey, so
  changing it would add churn and risk for no benefit.
