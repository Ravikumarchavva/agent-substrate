"""What an agent's one conversation says, read from the runtime's run logs (kernel `RuntimeStore`)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from loamz.engine.agents.kernel.runtime.store import RuntimeStore
from loamz.engine.agents.kernel.types.identity import Actor
from loamz.engine.agents.kernel.types.run_log import RunLogKind

PREVIEW_CHARS = 140


@dataclass(frozen=True)
class LastWord:
    text: str
    at: datetime | None = None


@dataclass(frozen=True)
class Said:
    role: str
    """`user` or `assistant`."""
    text: str
    at: datetime


class ThreadActivity(Protocol):
    async def last_word(self, thread_id: str) -> LastWord | None: ...

    async def working(self, thread_ids: Sequence[str]) -> set[str]: ...

    async def said(self, thread_id: str) -> list[Said]: ...


class RuntimeActivity:
    def __init__(self, store: RuntimeStore) -> None:
        self._store = store

    async def last_word(self, thread_id: str) -> LastWord | None:
        """The assistant's latest answer, else the user's latest message, as a one-line preview."""
        for run in reversed(await self._store.find_runs(thread_id=thread_id, active_only=False)):
            said = {RunLogKind.ASSISTANT_MESSAGE: "", RunLogKind.USER_MESSAGE: ""}
            for entry in await self._store.read_events(run.run_id, durable_only=True):
                if entry.kind in said:
                    text = str((entry.payload or {}).get("text") or "").strip()
                    if text:
                        said[RunLogKind(entry.kind)] = text
            text = said[RunLogKind.ASSISTANT_MESSAGE] or said[RunLogKind.USER_MESSAGE]
            if text:
                line = " ".join(text.split())
                return LastWord(
                    line if len(line) <= PREVIEW_CHARS else line[:PREVIEW_CHARS] + "…",
                    run.terminated_at or run.started_at or run.enqueued_at,
                )
        return None

    async def working(self, thread_ids: Sequence[str]) -> set[str]:
        busy = await self._store.working([Actor("assistant", t) for t in thread_ids])
        return {a.key for a in busy}

    async def said(self, thread_id: str) -> list[Said]:
        out: list[Said] = []
        for run in await self._store.find_runs(thread_id=thread_id, active_only=False):
            for entry in await self._store.read_events(run.run_id, durable_only=True):
                text = str((entry.payload or {}).get("text") or "")
                if not text.strip():
                    continue
                if entry.kind == RunLogKind.USER_MESSAGE:
                    out.append(Said("user", text, entry.ts))
                elif entry.kind == RunLogKind.ASSISTANT_MESSAGE:
                    out.append(Said("assistant", text, entry.ts))
        return out
