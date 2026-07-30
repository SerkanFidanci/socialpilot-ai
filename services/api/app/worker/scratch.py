"""Bounded worker scratch enforcement for a single-server deployment.

On one machine the worker's temporary directory shares the host with PostgreSQL, the API,
and every other tenant's job. A render or analysis job that fills scratch silently would
take the whole box down, so the drain loop enforces a soft budget *before* it hands more
work to a service, and reclaims orphaned scratch left by a crashed prior worker generation.

Two layers protect the disk and neither is silent:

1. **This soft guard** (application level): the drain refuses to start another job while the
   scratch root is over budget, failing loudly with ``WORKER_SCRATCH_BUDGET_EXCEEDED``
   instead of marching into a full filesystem. The budget sits below the hard tmpfs wall so
   the guard trips first, with headroom to spare.
2. **The hard tmpfs cap** (``compose.yaml`` ``tmpfs size=``): a single runaway job that
   outruns the soft check still hits ``ENOSPC`` on the next write, which fails that job
   through the service's normal error path rather than exhausting host memory.

The budget is derived from the worker tmpfs size so the two layers move together: change the
tmpfs size in Compose and the soft budget follows.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from app.core.config import WORKER_TMPFS_BYTES

# Keep a 25% margin below the hard tmpfs wall. The soft guard must trip *before* the
# filesystem returns ENOSPC mid-write, so a job fails on a documented budget check with room
# to unwind rather than on a half-written frame or audio file. Derived from the Compose tmpfs
# size (WORKER_TMPFS_BYTES) so the application budget cannot drift above the OS limit.
WORKER_SCRATCH_MAX_BYTES = WORKER_TMPFS_BYTES * 3 // 4


class WorkerScratchExhausted(RuntimeError):
    """Scratch usage crossed the soft budget; the drain must not start more work."""

    error_code = "WORKER_SCRATCH_BUDGET_EXCEEDED"

    def __init__(self, *, usage_bytes: int, max_bytes: int) -> None:
        super().__init__(
            f"{self.error_code}: worker scratch {usage_bytes} bytes exceeds "
            f"budget {max_bytes} bytes"
        )
        self.usage_bytes = usage_bytes
        self.max_bytes = max_bytes


class WorkerScratchGuard:
    """Measure, reclaim, and budget-check the worker's scratch root."""

    def __init__(self, root: Path, max_bytes: int = WORKER_SCRATCH_MAX_BYTES) -> None:
        self._root = root
        self._max_bytes = max_bytes

    @property
    def max_bytes(self) -> int:
        return self._max_bytes

    def usage_bytes(self) -> int:
        """Total size of every file under the scratch root.

        A file that vanishes mid-walk (a service cleaning its own ``TemporaryDirectory``
        concurrently) is skipped rather than raising: the measurement is advisory and must
        never itself crash a drain.
        """

        total = 0
        for path in self._root.rglob("*"):
            try:
                if path.is_file() and not path.is_symlink():
                    total += path.stat().st_size
            except OSError:
                continue
        return total

    def reclaim_stale(self) -> int:
        """Delete every entry directly under the scratch root; return bytes reclaimed.

        Services clean their own ``TemporaryDirectory`` on success or failure, but a worker
        killed mid-job (OOM, SIGKILL) leaves its scratch behind. Reclaiming at process init —
        and again after each drained job — keeps orphans from accumulating across restarts.
        """

        if not self._root.exists():
            return 0
        reclaimed = 0
        for entry in self._root.iterdir():
            try:
                if entry.is_dir() and not entry.is_symlink():
                    reclaimed += _directory_size(entry)
                    shutil.rmtree(entry, ignore_errors=True)
                else:
                    reclaimed += entry.stat().st_size
                    entry.unlink(missing_ok=True)
            except OSError:
                continue
        return reclaimed

    def ensure_within_budget(self) -> None:
        """Raise ``WorkerScratchExhausted`` if scratch usage is over budget."""

        usage = self.usage_bytes()
        if usage > self._max_bytes:
            raise WorkerScratchExhausted(usage_bytes=usage, max_bytes=self._max_bytes)


def _directory_size(root: Path) -> int:
    total = 0
    for path in root.rglob("*"):
        try:
            if path.is_file() and not path.is_symlink():
                total += path.stat().st_size
        except OSError:
            continue
    return total
