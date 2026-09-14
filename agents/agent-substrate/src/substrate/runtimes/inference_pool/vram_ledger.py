"""``VramLedger`` — a real per-GPU VRAM budget tracker for arbitrating
concurrent reservations *within one process*.

Real finding, confirmed via code reading (not assumed): VRAM budgeting is
currently reinvented independently in at least three places with no shared
owner — ``integrations/llm/factory.py``'s ``sentence_transformers`` branch
hardcodes ``device="cpu"`` citing a real, reproduced CUDA OOM collision with
document-intelligence; ``document_intelligence/service/autoconfig.py``'s
``_GPU_ELIGIBLE_MIB``/batch-size constants encode independently-derived VRAM
lore from real OOM crashes hit tuning PaddleOCR-VL onto a 4GB card; and any
future ``llama-server``-shaped consumer would otherwise reinvent a third
version of the same bookkeeping.

**Honest scope boundary — read before reaching for this in a new place:**
this ledger is authoritative only within the process that constructs it. It
does **not** — and structurally cannot — fix the ``sentence_transformers``
case cited above: that collision is *cross-container* (the main backend
process and the document-intelligence service are always separate
processes/containers in this codebase's real deployment topology, docker-
compose or k8s alike), so no in-process ledger can see or arbitrate the
other side's reservations. That hardcoded ``device="cpu"`` stays the correct
permanent decision unless a genuinely different, shared (e.g. Redis-backed)
cross-process ledger is built — a separate design, not this module, and not
half-built here. The real, in-scope use of ``VramLedger`` is arbitrating
concurrent GPU consumers *inside one service's own process* — e.g.
document-intelligence's own VL-worker admission (see
``service/engines/factory.py``), where the layout-detection stage and a
local llama-server pool worker can land on the same GPU within one
container.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Lease:
    """A granted reservation — hold onto it to ``release()`` later. Not
    itself a context manager: callers here (``factory.py``'s startup-time
    worker admission) hold leases for the process's lifetime, not around a
    single call, so a context-manager shape would be misleading."""

    device: str
    mib: int


class VramLedger:
    """In-memory, ``asyncio.Lock``-protected MiB budget per device string
    (e.g. ``"gpu:0"``). Never touches an actual GPU/CUDA API itself — the
    caller supplies the starting budget (typically from
    ``hardware.HardwareProfile``'s real detected ``free_mib`` per GPU), and
    this only tracks arithmetic against it.
    """

    def __init__(self, free_mib_by_device: dict[str, int]) -> None:
        self._free = dict(free_mib_by_device)
        self._lock = asyncio.Lock()

    async def reserve(self, device: str, mib: int) -> Lease | None:
        """Grant a lease for *mib* on *device* if the budget allows it,
        else return ``None`` — never raises, since "doesn't fit" is a real,
        expected outcome the caller must handle (degrade a worker count,
        not crash startup)."""
        async with self._lock:
            available = self._free.get(device, 0)
            if mib > available:
                return None
            self._free[device] = available - mib
            return Lease(device=device, mib=mib)

    async def release(self, lease: Lease) -> None:
        """Idempotent-in-spirit only for a *distinct* lease object each
        time — releasing the same ``Lease`` instance twice double-credits
        the device. Callers must track their own leases and release each
        exactly once (matches this codebase's existing acquire/release
        pooling pattern elsewhere, e.g. ``InferencePool``)."""
        async with self._lock:
            self._free[lease.device] = self._free.get(lease.device, 0) + lease.mib

    def available(self, device: str) -> int:
        """Current remaining budget for *device*, 0 for a device never
        given a starting budget (not an error — a CPU-only "device" string
        naturally has none and callers should skip reserving against it)."""
        return self._free.get(device, 0)


__all__ = ["VramLedger", "Lease"]
