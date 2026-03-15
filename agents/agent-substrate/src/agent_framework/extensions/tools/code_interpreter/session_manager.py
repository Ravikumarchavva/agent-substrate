"""Session-based VM manager.

Maps session_id (e.g. a chat thread_id) to a persistent Firecracker VM.
Each session gets its own isolated microVM with:
  - Persistent Python namespace (variables survive between calls)
  - Full bash access
  - File read/write within the VM
  - 30-minute idle timeout before automatic cleanup

Usage::

    manager = SessionManager()
    await manager.start()

    # Same session_id → same VM, state persists
    r1 = await manager.execute("alice", {"type": "python", "code": "x = 42"})
    r2 = await manager.execute("alice", {"type": "python", "code": "print(x)"})
    # r2["output"] == "42\n"

    await manager.stop()
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from .config import CodeInterpreterConfig
from .vm_manager import VM, VMPool, VMState

logger = logging.getLogger(__name__)


@dataclass
class SessionInfo:
    """Metadata for an active session VM."""
    session_id: str
    vm: VM
    created_at: float = field(default_factory=time.monotonic)
    last_used: float = field(default_factory=time.monotonic)
    exec_count: int = 0

    def touch(self) -> None:
        self.last_used = time.monotonic()

    @property
    def idle_seconds(self) -> float:
        return time.monotonic() - self.last_used

    @property
    def age_seconds(self) -> float:
        return time.monotonic() - self.created_at

    def to_dict(self) -> dict:
        return {
            "session_id":   self.session_id,
            "vm_id":        self.vm.vm_id,
            "vm_state":     self.vm.state.name,
            "exec_count":   self.exec_count,
            "age_seconds":  round(self.age_seconds),
            "idle_seconds": round(self.idle_seconds),
        }


class SessionManager:
    """Manages persistent per-session Firecracker VMs.

    Architecture
    ────────────
    ┌─────────────────────────────────────────────────┐
    │ session "alice" → VM₁ (Python state + files)   │
    │ session "bob"   → VM₂ (Python state + files)   │
    │ session "carol" → VM₃ (Python state + files)   │
    └──────────────────────┬──────────────────────────┘
                           │ acquire (new session)
                           ▼
                   ┌──────────────┐
                   │  WarmVMPool  │  pre-booted, low-latency
                   └──────────────┘
    """

    def __init__(
        self,
        config: Optional[CodeInterpreterConfig] = None,
        pool: Optional[VMPool] = None,
    ):
        self.config = config or CodeInterpreterConfig()
        self._pool = pool or VMPool(self.config)
        self._manager = self._pool.manager
        self._sessions: dict[str, SessionInfo] = {}
        self._lock = asyncio.Lock()
        self._cleanup_task: Optional[asyncio.Task] = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the warm VM pool and background cleanup task."""
        await self._pool.start()
        self._cleanup_task = asyncio.create_task(
            self._cleanup_loop(), name="session-cleanup"
        )
        logger.info("SessionManager started")

    async def stop(self) -> None:
        """Destroy all session VMs and stop the warm pool."""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

        async with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()

        for si in sessions:
            await self._destroy_vm(si)

        await self._pool.stop()
        logger.info("SessionManager stopped (%d sessions closed)", len(sessions))

    # ── Session access ────────────────────────────────────────────────────────

    async def get_or_create(self, session_id: str) -> SessionInfo:
        """Return the session's VM, creating one if needed."""
        async with self._lock:
            if session_id in self._sessions:
                si = self._sessions[session_id]
                if si.vm.process and si.vm.process.poll() is None:
                    si.touch()
                    return si
                # VM died — remove stale entry and create fresh
                logger.warning("Session %s: VM died, recreating", session_id)
                del self._sessions[session_id]

            if len(self._sessions) >= self.config.max_sessions:
                raise RuntimeError(
                    f"Session limit reached ({self.config.max_sessions}). "
                    "End an existing session first."
                )

            vm = await self._pool.acquire(timeout=90.0)
            si = SessionInfo(session_id=session_id, vm=vm)
            self._sessions[session_id] = si
            logger.info("Session %s → VM %s", session_id, vm.vm_id)
            return si

    async def execute(self, session_id: str, request: dict) -> dict:
        """Execute a request inside session_id's VM.

        The VM (and its Python state) persists between calls.
        """
        si = await self.get_or_create(session_id)
        timeout = request.get("timeout", self.config.default_timeout)

        try:
            result = await self._manager.execute_request(si.vm, request, timeout)
        except Exception as exc:
            logger.error(
                "Session %s execution error: %s", session_id, exc, exc_info=True
            )
            result = {
                "success": False,
                "output": "",
                "stderr": "",
                "error": f"{type(exc).__name__}: {exc}",
            }

        si.exec_count += 1
        si.touch()
        return result

    async def reset_session(self, session_id: str) -> dict:
        """Clear Python state in a session without destroying its VM."""
        return await self.execute(session_id, {"type": "reset"})

    async def destroy_session(self, session_id: str) -> None:
        """Immediately shut down a session's VM."""
        async with self._lock:
            si = self._sessions.pop(session_id, None)
        if si:
            await self._destroy_vm(si)
            logger.info("Session %s destroyed", session_id)

    def list_sessions(self) -> list[dict]:
        """Return a snapshot of all active sessions."""
        return [si.to_dict() for si in self._sessions.values()]

    @property
    def session_count(self) -> int:
        return len(self._sessions)

    # ── Background cleanup ────────────────────────────────────────────────────

    async def _cleanup_loop(self) -> None:
        """Destroy sessions that have been idle longer than session_timeout."""
        while True:
            try:
                await asyncio.sleep(60)
                await self._evict_expired()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Cleanup error: %s", exc, exc_info=True)

    async def _evict_expired(self) -> None:
        threshold = self.config.session_timeout
        async with self._lock:
            expired = [
                si for si in self._sessions.values()
                if si.idle_seconds > threshold
            ]
            for si in expired:
                del self._sessions[si.session_id]

        for si in expired:
            logger.info(
                "Session %s expired (idle=%.0fs), destroying VM %s",
                si.session_id, si.idle_seconds, si.vm.vm_id,
            )
            await self._destroy_vm(si)

    async def _destroy_vm(self, si: SessionInfo) -> None:
        """Graceful shutdown → destroy VM → replenish warm pool."""
        # Ask guest agent to power down gracefully (best-effort)
        try:
            await asyncio.wait_for(
                self._manager.execute_request(si.vm, {"type": "shutdown"}, timeout=3),
                timeout=5,
            )
        except Exception:
            pass

        await self._manager.destroy_vm(si.vm)
        # Keep warm pool topped up
        self._pool._schedule_replenish()
