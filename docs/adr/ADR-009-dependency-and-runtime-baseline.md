# ADR-009: Dependency and Runtime Baseline

**Status:** Accepted
**Date:** 2026-07-30
**Supersedes nothing. Establishes the dependency-management, Python-runtime, and CI-security
baseline for the backend service.**

## Context

The backend's dependency pins were all written from memory during Phase 0. Every version
resolved to a September–November 2024 release, none had been verified against the package
registry, and — decisively — every pin carried an **upper bound** (`fastapi<0.116`,
`celery<5.5`, `alembic<1.15`, `redis<6.0`, and `<3.13` on Python itself). That shape does not
protect reproducibility; a lockfile does. What the caps actually did was block every routine
upgrade, so the project would drift further from current releases the longer it ran.

Three more gaps compounded this:

- **No lockfile.** `requirements.txt` and `pyproject.toml` held overlapping ranges, so CI and
  production could resolve different bytes. There was no single artifact that pinned the exact
  transitive closure.
- **No security scanning in CI**, although PRD §41.1 lists "Security scan" as a required
  pull-request gate and §33.5 enumerates dependency, container, and secret scanning.
- **No automated update mechanism** (Dependabot/Renovate), so keeping current was manual —
  the same manual process that produced the stale, from-memory pins in the first place.

## Decision

### uv with a committed lockfile

Adopt [`uv`](https://docs.astral.sh/uv/) as the package manager. `pyproject.toml` declares
dependencies with **lower bounds only**; `uv.lock` pins the exact resolved closure and is
committed. Every install path — the Dockerfile and all CI jobs — uses `uv sync --locked`, which
fails if the lock and the manifest disagree. `requirements.txt` is removed; the lockfile is now
the single reproducibility contract. uv is the de-facto standard for this stack and resolves the
full graph in well under a second, so lock maintenance is cheap enough to run weekly.

Upper caps are removed because reproducibility is the lockfile's job, not the range's. The one
deliberate exception is **SQLAlchemy, capped at `<2.1`**: 2.1 is still beta. The cap carries an
inline comment so it is not mistaken for the old from-memory style.

### Versions verified against the registry, then frozen

Every dependency was refreshed to its current stable release **verified against PyPI at the
moment of writing**, never from memory, and frozen in the lock. The notable major-version moves
and their fallout:

- **mypy 1.x → 2.x** reclassified Celery's untyped task decorators from `misc` to
  `untyped-decorator` and tightened `Any` inference. Handled with two narrowly scoped
  `pyproject` overrides (Celery decorators confined to the two task modules; the script-only
  sibling import) plus three `int(...)` wraps in tests and the removal of one now-redundant
  cast — no strictness was dropped globally.
- **ruff 0.8 → 0.16** with the `py313` target applied `UP043` (`Generator[None, None, None]`
  → `Generator[None]`) across the integration tests.
- **fastapi 0.115 → 0.141** now emits pydantic's `ctx`/`input` fields in the generated
  `ValidationError` schema; the committed OpenAPI contract was regenerated to match.

### Python 3.13

`requires-python` becomes `>=3.13`; the `<3.13` bound is gone. The Dockerfile base is
`python:3.13-slim`, CI resolves the interpreter through `UV_PYTHON=3.13`, and ruff/mypy target
`py313`/`3.13`. PostgreSQL 18 and the Valkey question are deliberately **not** in this baseline;
they touch `compose.yaml` and are handled by W06 ([ADR-010](ADR-010-valkey-runtime-evaluation.md)
records the Valkey recommendation only).

### CI security gates (PRD §41.1, §33.5)

Three gates are added to `verify.yml`, each with an explicit, documented threshold that can
deliberately turn the build red:

| Gate | Tool | Threshold |
|---|---|---|
| Dependency vulnerability scan | `pip-audit` over the exported lock | any PyPA/OSV advisory |
| Secret scan (full git history) | `gitleaks` | any finding not allowlisted in `.gitleaks.toml` |
| Container image scan | `trivy` on the built API image | a **fixable** CRITICAL OS/library CVE |

Container findings with no available fix are reported but do not block, because no upgrade can
clear them. Known development-only placeholder credentials are allowlisted for gitleaks so the
gate flags real leaks rather than fixtures.

### Automated updates

`renovate.json` runs weekly, groups the Python backend dependencies into one PR and CI
actions/base images into another, and refreshes `uv.lock` on every update
(`rangeStrategy: update-lockfile`). The SQLAlchemy major hold is encoded as a disabled rule so
the intent survives independently of the manifest comment.

## Consequences

- CI and production install the identical, lockfile-pinned closure; an unpinned or drifted
  dependency now fails `uv sync --locked`.
- A newly disclosed vulnerability, a committed secret anywhere in history, or a fixable critical
  image CVE each fails the build with a documented threshold.
- The image build is a two-layer uv sync (dependencies, then project) with the environment at
  `/opt/venv`, deliberately outside the source tree so the Compose read-only bind mount cannot
  shadow it.
- Weekly Renovate PRs keep the baseline from decaying back into a stale, from-memory state; the
  cost is a small, recurring review load.
- The security jobs add container-build and history-scan time to CI, run as independent jobs so
  a failure is attributable and the backend job is unaffected.

## Rejected alternatives

- **Keeping `pip` + `requirements.txt` with hashes:** reproducible, but no fast native
  lock/update loop, weaker Renovate integration for the resolved graph, and it leaves two
  overlapping manifests to keep in sync. uv is already the de-facto standard for this stack.
- **Keeping the upper caps and bumping only the numbers:** the caps were the thing actively
  blocking upgrades; a lockfile makes them redundant, and reproducibility is not their real job.
- **Pinning mypy/ruff to their old 1.x/0.8 lines to avoid fallout:** freezes the toolchain on a
  known-stale baseline for the sake of a handful of mechanical, in-slice fixes — the opposite of
  this ADR's intent.
- **Poetry / PDM:** capable, but slower resolution and not the stack's de-facto standard; uv
  already backs the lockfile-first workflow this repository wants.
