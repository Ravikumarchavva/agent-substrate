"""Shared contract between ``llama_pool.py`` (the subprocess/remote
supervisor) and ``engines/paddle_vl.py`` (the consumer) — defined once,
up front, so both can be implemented independently without either
guessing at the other's shape.

Kept deliberately separate from ``llama_pool.py`` itself: this file has
zero heavy imports (no ``asyncio.subprocess``, no ``httpx``), so anything
that only needs the *shape* of a worker/pool (tests, ``paddle_vl.py``'s
type hints) doesn't have to import the real subprocess-lifecycle code.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from substrate.integrations.llm.endpoint import InferenceEndpoint


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


__all__ = ["PoolWorker", "InferencePool"]
