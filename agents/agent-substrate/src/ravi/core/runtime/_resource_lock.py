"""Resource lock manager — prevents concurrent sub-agents from corrupting shared resources.

Provides advisory locking with two modes:

- **Exclusive** — only one agent can hold (for writes / mutations).
- **Shared** — multiple agents can hold concurrently (for reads).

Deadlock detection uses a wait-for graph: before an agent blocks on a lock,
we add an edge ``waiter → holder`` and check for cycles.  If a cycle is
found, ``DeadlockDetectedError`` is raised on the *waiter* to break the
deadlock without affecting the holders.

Lock state is serialisable so it can be included in checkpoints — on
recovery the runtime knows which resources were held and can re-acquire
or release them cleanly.

Usage::

    lock_mgr = ResourceLockManager()
    handle = await lock_mgr.acquire("file:///workspace/a.py", agent_id="coder-1", mode="exclusive")
    try:
        # ... mutate the file ...
    finally:
        await lock_mgr.release(handle)
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from ravi.core.runtime._errors import (
    DeadlockDetectedError,
    ResourceConflictError,
)

logger = logging.getLogger("ravi.core.runtime.resource_lock")


# ---------------------------------------------------------------------------
# Lock mode
# ---------------------------------------------------------------------------


class LockMode(str, Enum):
    """Advisory lock modes."""

    EXCLUSIVE = "exclusive"
    SHARED = "shared"


# ---------------------------------------------------------------------------
# Lock handle (returned to callers)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LockHandle:
    """Opaque handle returned by ``acquire()``.

    The caller passes this to ``release()`` to free the lock.  The handle
    is frozen and hashable so it can be stored in sets/dicts.
    """

    handle_id: str
    resource_uri: str
    agent_id: str
    mode: LockMode
    acquired_at: datetime

    def to_dict(self) -> dict[str, object]:
        """Serialise for checkpointing."""
        return {
            "handle_id": self.handle_id,
            "resource_uri": self.resource_uri,
            "agent_id": self.agent_id,
            "mode": self.mode.value,
            "acquired_at": self.acquired_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "LockHandle":
        """Deserialise from checkpoint data."""
        return cls(
            handle_id=str(data["handle_id"]),
            resource_uri=str(data["resource_uri"]),
            agent_id=str(data["agent_id"]),
            mode=LockMode(str(data["mode"])),
            acquired_at=datetime.fromisoformat(str(data["acquired_at"])),
        )


# ---------------------------------------------------------------------------
# Internal lock record
# ---------------------------------------------------------------------------


@dataclass
class _LockRecord:
    """Tracks all holders and waiters for a single resource."""

    resource_uri: str
    mode: LockMode | None = None  # None when no holders
    holders: dict[str, LockHandle] = field(default_factory=dict)  # handle_id → LockHandle
    waiters: list[tuple[str, LockMode, asyncio.Event]] = field(
        default_factory=list
    )  # (agent_id, mode, event)


# ---------------------------------------------------------------------------
# ResourceLockManager
# ---------------------------------------------------------------------------


class ResourceLockManager:
    """Advisory lock manager with deadlock detection.

    All operations are async-safe (single event loop) and use
    ``asyncio.Event`` for blocking waiters — no threads involved.

    Parameters
    ----------
    default_timeout:
        Default seconds to wait for a lock before raising ``ResourceConflictError``.
        ``None`` disables the timeout (blocks indefinitely — not recommended).
    """

    __slots__ = ("_locks", "_wait_for_graph", "_default_timeout")

    def __init__(self, default_timeout: float | None = 30.0) -> None:
        self._locks: dict[str, _LockRecord] = {}
        # Wait-for graph: waiter_agent_id → set of holder_agent_ids
        self._wait_for_graph: dict[str, set[str]] = {}
        self._default_timeout = default_timeout

    # -- public API ---------------------------------------------------------

    async def acquire(
        self,
        resource_uri: str,
        agent_id: str,
        *,
        mode: LockMode | str = LockMode.EXCLUSIVE,
        timeout: float | None = None,
    ) -> LockHandle:
        """Acquire a lock on *resource_uri*.

        Blocks until the lock is available, up to *timeout* seconds.

        Raises:
            DeadlockDetectedError: if acquiring would create a wait-for cycle.
            ResourceConflictError: if the timeout expires.
        """
        if isinstance(mode, str):
            mode = LockMode(mode)

        effective_timeout = timeout if timeout is not None else self._default_timeout
        record = self._locks.setdefault(resource_uri, _LockRecord(resource_uri=resource_uri))

        # Fast path: can grant immediately?
        if self._can_grant(record, agent_id, mode):
            return self._grant(record, agent_id, mode)

        # Deadlock detection before blocking
        holder_ids = {h.agent_id for h in record.holders.values()}
        self._wait_for_graph.setdefault(agent_id, set()).update(holder_ids)
        cycle = self._detect_cycle(agent_id)
        if cycle:
            self._wait_for_graph.pop(agent_id, None)
            raise DeadlockDetectedError(cycle)

        # Block until grant or timeout
        event = asyncio.Event()
        record.waiters.append((agent_id, mode, event))
        logger.debug(
            "agent %s waiting for %s lock on %s (timeout=%s)",
            agent_id, mode.value, resource_uri, effective_timeout,
        )

        try:
            if effective_timeout is not None:
                await asyncio.wait_for(event.wait(), timeout=effective_timeout)
            else:
                await event.wait()
        except asyncio.TimeoutError:
            # Clean up waiter entry and wait-for graph
            record.waiters = [
                (a, m, e) for a, m, e in record.waiters
                if not (a == agent_id and e is event)
            ]
            self._wait_for_graph.pop(agent_id, None)
            # Find one of the holders for the error message
            holder_id = next(iter(holder_ids), "unknown")
            raise ResourceConflictError(
                resource_uri, holder_id,
                f"timeout waiting for {mode.value} lock on {resource_uri!r}"
            ) from None
        finally:
            self._wait_for_graph.pop(agent_id, None)

        # Re-check and grant (the waker already verified compatibility)
        return self._grant(record, agent_id, mode)

    async def release(self, handle: LockHandle) -> None:
        """Release a previously acquired lock."""
        record = self._locks.get(handle.resource_uri)
        if record is None:
            logger.warning("release called for unknown resource %s", handle.resource_uri)
            return

        removed = record.holders.pop(handle.handle_id, None)
        if removed is None:
            logger.warning("release called for unknown handle %s", handle.handle_id)
            return

        logger.debug(
            "agent %s released %s lock on %s",
            handle.agent_id, handle.mode.value, handle.resource_uri,
        )

        # If no holders remain, reset mode
        if not record.holders:
            record.mode = None

        # Wake compatible waiters
        self._wake_waiters(record)

        # Clean up empty records
        if not record.holders and not record.waiters:
            self._locks.pop(handle.resource_uri, None)

    def is_locked(self, resource_uri: str) -> bool:
        """Return True if the resource has any active holders."""
        record = self._locks.get(resource_uri)
        return record is not None and bool(record.holders)

    def get_holders(self, resource_uri: str) -> list[LockHandle]:
        """Return all lock handles currently held on a resource."""
        record = self._locks.get(resource_uri)
        if record is None:
            return []
        return list(record.holders.values())

    def get_agent_locks(self, agent_id: str) -> list[LockHandle]:
        """Return all locks held by a specific agent (across all resources)."""
        result: list[LockHandle] = []
        for record in self._locks.values():
            for handle in record.holders.values():
                if handle.agent_id == agent_id:
                    result.append(handle)
        return result

    async def release_all_for_agent(self, agent_id: str) -> int:
        """Release all locks held by *agent_id*.  Returns count released."""
        handles = self.get_agent_locks(agent_id)
        for handle in handles:
            await self.release(handle)
        return len(handles)

    def snapshot(self) -> list[dict[str, object]]:
        """Serialise all active locks for checkpointing."""
        result: list[dict[str, object]] = []
        for record in self._locks.values():
            for handle in record.holders.values():
                result.append(handle.to_dict())
        return result

    # -- internal -----------------------------------------------------------

    @staticmethod
    def _can_grant(record: _LockRecord, agent_id: str, mode: LockMode) -> bool:
        """Check if the lock can be granted immediately."""
        if not record.holders:
            return True  # no holders → always grantable
        if mode == LockMode.SHARED and record.mode == LockMode.SHARED:
            return True  # shared + shared = compatible
        # Check if the same agent already holds it (re-entrant)
        for handle in record.holders.values():
            if handle.agent_id == agent_id:
                return True  # same agent, allow re-entrant
        return False

    def _grant(
        self, record: _LockRecord, agent_id: str, mode: LockMode
    ) -> LockHandle:
        """Create a handle and register it as a holder."""
        handle = LockHandle(
            handle_id=uuid.uuid4().hex,
            resource_uri=record.resource_uri,
            agent_id=agent_id,
            mode=mode,
            acquired_at=datetime.now(timezone.utc),
        )
        record.holders[handle.handle_id] = handle
        record.mode = mode
        logger.debug(
            "granted %s lock on %s to agent %s",
            mode.value, record.resource_uri, agent_id,
        )
        return handle

    def _wake_waiters(self, record: _LockRecord) -> None:
        """Wake waiters that are now compatible with the current lock state."""
        remaining: list[tuple[str, LockMode, asyncio.Event]] = []
        for agent_id, mode, event in record.waiters:
            if self._can_grant(record, agent_id, mode):
                event.set()
                # Don't grant here — the waiter's coroutine calls _grant after waking
            else:
                remaining.append((agent_id, mode, event))
        record.waiters = remaining

    def _detect_cycle(self, start: str) -> list[str] | None:
        """DFS on the wait-for graph to find a cycle involving *start*."""
        visited: set[str] = set()
        path: list[str] = []

        def _dfs(node: str) -> bool:
            if node in visited:
                if node == start:
                    path.append(node)
                    return True
                return False
            visited.add(node)
            path.append(node)
            for neighbour in self._wait_for_graph.get(node, set()):
                if _dfs(neighbour):
                    return True
            path.pop()
            return False

        for target in self._wait_for_graph.get(start, set()):
            path = [start]
            visited = {start}
            if _dfs(target):
                return path
        return None
