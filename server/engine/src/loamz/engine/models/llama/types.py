"""The contract between a pool of model servers (``pool.py``) and what dispatches to it (the reader's
PaddleOCR-VL engine, the data server's ``inference`` role): where to reach one worker, and what a pool does.

No heavy imports here (no subprocesses), so anything that only needs the *shape* of a worker or pool can import it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class InferenceEndpoint:
    """Where to reach a model server: ``model`` is ``"provider/bare-model-name"``; local pooled children and remote
    deployments both resolve to one of these, so a caller never needs to know which it has."""

    model: str
    base_url: str | None = None
    api_key: str = ""
    timeout_s: float = 90.0


@dataclass(slots=True)
class PoolWorker:
    """One acquired unit of VL inference capacity — a live
    :class:`InferenceEndpoint` to build an ``LLMFactory`` client against,
    plus the device the *layout* stage (still classic Paddle models) should
    run on for this same worker. Local mode: ``endpoint.base_url`` points at
    a subprocess this pool spawned, pinned to ``layout_device`` via
    ``CUDA_VISIBLE_DEVICES``. Remote mode: ``endpoint.base_url`` points at
    a pre-existing deployment, ``layout_device`` is still whatever's locally
    available (layout detection always runs locally — only VL recognition
    can be remote)."""

    index: int
    endpoint: InferenceEndpoint
    layout_device: str


class InferencePool(Protocol):
    """What ``paddle_vl.py`` needs from either a local subprocess pool or a
    remote (pre-existing-endpoints) pool — callers cannot tell which they
    have, and never branch on it. This is the rule that makes "today
    llama.cpp, tomorrow sglang/vLLM/a remote URL" an actual config change:
    the pool's output is always just a client-buildable endpoint."""

    #: True once at least one worker is healthy and dispatch-ready.
    ready: bool
    #: How many workers this pool actually has (may be < requested if some
    #: failed to start — partial success is accepted, never all-or-nothing).
    worker_count: int

    async def start(self) -> None:
        """Bring the pool up: spawn/health-gate local children, or just
        validate remote endpoints are configured. Never raises on partial
        failure — a pool with zero healthy workers sets ``ready = False``
        and the caller (``engines/factory.py``) degrades to another mode."""
        ...

    async def acquire(self) -> PoolWorker:
        """Wait for and return an idle worker (queue-based, least-loaded —
        not round-robin, since document sizes vary by orders of magnitude
        and round-robin would queue a 1-page request behind a 267-page one
        on a busy worker while another sits idle)."""
        ...

    def release(self, worker: PoolWorker) -> None:
        """Return a worker to the idle queue. Always called from a
        ``finally``, matching the ``acquire``/``release`` pairing pattern
        used everywhere else a pool is checked out in this codebase."""
        ...

    async def aclose(self) -> None:
        """Graceful shutdown: local pools terminate all children
        (``terminate()`` -> wait with timeout -> ``kill()``); remote pools
        do nothing (nothing was spawned). Idempotent — safe to call more
        than once."""
        ...


__all__ = ["InferenceEndpoint", "InferencePool", "PoolWorker"]
