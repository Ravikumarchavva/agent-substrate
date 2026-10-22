"""A conversation read from the runtime's run journal, the one record of what was said.

A thread's runs (oldest first) each contribute their streaming wire events to one chronological list; live streaming,
reconnect and history are three views over the same entries through the same `wire_from_log` mapping, so there is no
second store to drift from the journal. Counting and searching go to the journal's tables directly (the runtime owns
them; this is a read model over them, keyed by tenant).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from loamz.engine.agents.kernel.runtime import NewEntry, Runtime
from loamz.engine.agents.kernel.types.run_log import RunLogKind
from loamz.engine.agents.kernel.wire import WireEvent, wire_from_log
from loamz.engine.agents.stores import AgentStore
from loamz.engine.sql import Row, Tx
from loamz.server.agents.features.conversations.threads.model import LastWord, RunDetail, ToolStep

MESSAGE_KINDS = ("user.message", "assistant.message")
MAX_THREADS = 500
MAX_RUNS = 100
SNIPPET_CHARS = 140
CUT = 400


@dataclass(frozen=True)
class Hit:
    thread_id: str
    role: str
    snippet: str
    seq: int


def _marks(n: int) -> str:
    return ",".join("?" * n)


def _like(query: str) -> str:
    escaped = query.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def snippet(text: str, query: str) -> str:
    """The words around the first match of `query` in `text`, on one line."""
    flat = " ".join(text.split())
    at = flat.lower().find(query.lower())
    if at < 0:
        return flat[:SNIPPET_CHARS]
    start = max(0, at - SNIPPET_CHARS // 3)
    cut = flat[start : start + SNIPPET_CHARS]
    return ("…" if start else "") + cut + ("…" if start + SNIPPET_CHARS < len(flat) else "")


def _cut(text: object) -> str:
    s = text if isinstance(text, str) else repr(text)
    return s if len(s) <= CUT else s[:CUT] + "…"


def _args(args: object) -> str:
    try:
        return _cut(json.dumps(args, ensure_ascii=False, default=str))
    except TypeError, ValueError:
        return _cut(args)


class ThreadJournal:
    def __init__(self, runtime: Runtime, store: AgentStore) -> None:
        self._runtime = runtime
        self._store = store

    @property
    def _rt(self):
        return self._runtime.store

    async def project(self, thread_id: str) -> list[tuple[WireEvent, datetime]]:
        """The whole conversation as ordered wire events with the time each was logged."""
        events: list[tuple[WireEvent, datetime]] = []
        for run in await self._rt.find_runs(thread_id=thread_id, active_only=False):
            for entry in await self._rt.read_events(run.run_id, durable_only=True):
                wire = wire_from_log(entry.kind, entry.payload or {}, history=True)
                if wire is not None:
                    events.append((wire, entry.ts))
        return events

    async def annotate(self, thread_id: str, kind: str, payload: dict[str, object]) -> bool:
        """Append an entry to the thread's active run (else its latest) for writes that do not come from a running
        agent: an MCP app's context, a note between runs. False if the thread has no runs yet."""
        runs = await self._rt.find_runs(thread_id=thread_id)
        if not runs:
            runs = await self._rt.find_runs(thread_id=thread_id, active_only=False)
        if not runs:
            return False
        target = (
            runs[0] if runs[0].status.value in ("pending", "running", "suspended") else runs[-1]
        )
        await self._rt.annotate(target.run_id, [NewEntry(kind=kind, payload=payload)])
        return True

    async def inspect(self, thread_id: str) -> list[RunDetail]:
        """What the conversation's most recent runs did: time, model calls, tokens, cost, tools and outcomes."""
        runs = (await self._rt.find_runs(thread_id=thread_id, active_only=False))[-MAX_RUNS:]
        out: list[RunDetail] = []
        for run in runs:
            detail = RunDetail(
                run_id=str(run.run_id),
                status=run.status.value,
                started_at=run.started_at or run.enqueued_at,
            )
            if run.started_at and run.terminated_at:
                detail.duration_ms = int(
                    (run.terminated_at - run.started_at).total_seconds() * 1000
                )
            by_call: dict[str, ToolStep] = {}
            for entry in await self._rt.read_events(run.run_id):
                p = entry.payload or {}
                if entry.kind == RunLogKind.USER_MESSAGE and not detail.user_message:
                    detail.user_message = _cut(p.get("text", ""))
                elif entry.kind == RunLogKind.LLM_CALL:
                    detail.llm_calls += 1
                    detail.model = str(p.get("model") or detail.model or "") or None
                    try:
                        detail.tokens += int(p.get("tokens") or 0)
                        detail.cost_usd += float(p.get("cost_usd") or 0.0)
                    except TypeError, ValueError:
                        pass
                elif entry.kind == RunLogKind.TOOL_CALL:
                    step = ToolStep(
                        call_id=str(p.get("call_id") or ""),
                        name=str(p.get("tool_name") or "tool"),
                        args=_args(p.get("args") or {}),
                        risk=p.get("risk"),
                    )
                    detail.tools.append(step)
                    if step.call_id:
                        by_call[step.call_id] = step
                elif entry.kind == RunLogKind.TOOL_RESULT:
                    step = by_call.get(str(p.get("call_id") or ""))
                    if step is not None:
                        step.ok = bool(p.get("ok", True))
                        step.output = _cut(p.get("error") or p.get("output") or "")
                elif entry.kind == RunLogKind.RUN_FAILED:
                    detail.error = _cut(p.get("error") or "The run failed.")
            out.append(detail)
        return out

    async def last_message(self, thread_id: str, *, limit: int = 140) -> LastWord | None:
        """The last thing said, as a one-line preview: the assistant's latest answer, else the user's."""
        runs = await self._rt.find_runs(thread_id=thread_id, active_only=False)
        for run in reversed(runs):
            said = {RunLogKind.ASSISTANT_MESSAGE: "", RunLogKind.USER_MESSAGE: ""}
            for entry in await self._rt.read_events(run.run_id, durable_only=True):
                if entry.kind in said:
                    text = str((entry.payload or {}).get("text") or "").strip()
                    if text:
                        said[entry.kind] = text
            text = said[RunLogKind.ASSISTANT_MESSAGE] or said[RunLogKind.USER_MESSAGE]
            if text:
                line = " ".join(text.split())
                return LastWord(
                    text=line if len(line) <= limit else line[:limit] + "…",
                    at=run.terminated_at or run.started_at or run.enqueued_at,
                )
        return None

    async def message_counts(self, tenant_id: str, thread_ids: list[str]) -> dict[str, int]:
        """Messages (said by the user, answered by the assistant) in each of `thread_ids`."""
        ids = thread_ids[:MAX_THREADS]
        if not ids:
            return {}

        async def op(tx: Tx) -> dict[str, int]:
            rows = await tx.fetchall(
                "SELECT r.thread_id AS thread_id, COUNT(*) AS n FROM rt_events e "
                "JOIN rt_runs r ON r.run_id = e.run_id "
                f"WHERE r.tenant = ? AND e.kind IN (?, ?) AND r.thread_id IN ({_marks(len(ids))}) "
                "GROUP BY r.thread_id",
                tenant_id,
                *MESSAGE_KINDS,
                *ids,
            )
            return {r["thread_id"]: int(r["n"]) for r in rows}

        return await self._store.run(op)

    async def search(
        self, tenant_id: str, thread_ids: list[str], query: str, limit: int = 30
    ) -> list[Hit]:
        """Messages containing `query` (case-insensitive) in any of `thread_ids`, newest first. The ids come from the
        caller's own ownership-checked list; a thread id is never taken from a request here."""
        query = query.strip()
        ids = thread_ids[:MAX_THREADS]
        if len(query) < 2 or not ids:
            return []

        async def op(tx: Tx) -> list[Row]:
            return list(
                await tx.fetchall(
                    "SELECT r.thread_id AS thread_id, e.seq AS seq, e.kind AS kind, e.entry_json AS entry "
                    "FROM rt_events e JOIN rt_runs r ON r.run_id = e.run_id "
                    f"WHERE r.tenant = ? AND e.kind IN (?, ?) AND r.thread_id IN ({_marks(len(ids))}) "
                    "AND lower(e.entry_json) LIKE ? ESCAPE '\\' ORDER BY e.run_id DESC, e.seq DESC LIMIT ?",
                    tenant_id,
                    *MESSAGE_KINDS,
                    *ids,
                    _like(query),
                    limit * 4,
                )
            )

        hits: list[Hit] = []
        for row in await self._store.run(op):
            try:
                text = str(json.loads(row["entry"]).get("payload", {}).get("text", ""))
            except ValueError, AttributeError:
                continue
            if query.lower() not in text.lower():
                continue
            role = "user" if row["kind"] == "user.message" else "assistant"
            hits.append(Hit(row["thread_id"], role, snippet(text, query), int(row["seq"])))
            if len(hits) >= limit:
                break
        return hits
