from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass(slots=True)
class SandboxSession:
    """Host-side record that maps one application thread to one sandbox."""

    thread_id: str
    claim_name: str
    namespace: str
    template: str
    sandbox_id: str | None = None
    user_id: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    # Bumped on every *read* too (see InMemorySessionStore.get), because a
    # session reused across many turns is alive even though nothing about it
    # changed. Using updated_at alone would let the idle reaper kill a sandbox
    # that is actively serving a long conversation.
    last_accessed: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "SandboxSession":
        return cls(
            thread_id=str(payload["thread_id"]),
            claim_name=str(payload["claim_name"]),
            namespace=str(payload["namespace"]),
            template=str(payload["template"]),
            sandbox_id=(
                str(payload["sandbox_id"])
                if payload.get("sandbox_id") is not None
                else None
            ),
            user_id=(
                str(payload["user_id"]) if payload.get("user_id") is not None else None
            ),
            created_at=float(payload.get("created_at") or time.time()),  # type: ignore[arg-type]
            updated_at=float(payload.get("updated_at") or time.time()),  # type: ignore[arg-type]
            last_accessed=float(payload.get("last_accessed") or time.time()),  # type: ignore[arg-type]
        )


class SessionStore(Protocol):
    """Storage contract for thread-aware sandbox sessions."""

    def get(self, thread_id: str) -> SandboxSession | None: ...

    def upsert(self, session: SandboxSession) -> None: ...

    def delete(self, thread_id: str) -> None: ...

    def list(self) -> list[SandboxSession]: ...

    def idle_since(self, cutoff: float) -> list[SandboxSession]: ...


class InMemorySessionStore:
    """Process-local session registry."""

    def __init__(self) -> None:
        self._sessions: dict[str, SandboxSession] = {}
        self._lock = threading.RLock()

    def get(self, thread_id: str) -> SandboxSession | None:
        with self._lock:
            session = self._sessions.get(thread_id)
            if session is not None:
                session.last_accessed = time.time()
            return session

    def upsert(self, session: SandboxSession) -> None:
        with self._lock:
            now = time.time()
            session.updated_at = now
            session.last_accessed = now
            self._sessions[session.thread_id] = session
            self._after_update()

    def delete(self, thread_id: str) -> None:
        with self._lock:
            self._sessions.pop(thread_id, None)
            self._after_update()

    def list(self) -> list[SandboxSession]:
        with self._lock:
            return list(self._sessions.values())

    def idle_since(self, cutoff: float) -> list[SandboxSession]:
        """Sessions untouched since *cutoff* — the reaper's input."""
        with self._lock:
            return [s for s in self._sessions.values() if s.last_accessed < cutoff]

    def _after_update(self) -> None:
        """Hook for persistent stores."""


class JsonSessionStore(InMemorySessionStore):
    """Small durable store for reconnecting thread sessions after restarts."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        super().__init__()
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return

        with self.path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)

        sessions = payload.get("sessions", [])
        with self._lock:
            self._sessions = {
                session.thread_id: session
                for session in (SandboxSession.from_dict(item) for item in sessions)
            }

    def _after_update(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
        payload = {"sessions": [session.to_dict() for session in self.list()]}

        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
        tmp_path.replace(self.path)
