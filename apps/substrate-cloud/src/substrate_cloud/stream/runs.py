"""What happened in a conversation's runs, in a form a person can inspect: how long each took, what it cost, which tools it called and whether they worked.

Read from the run journal like usage and export are, so it is exactly what the engine did rather than a second record kept beside it. Long text
(a tool's output) is cut: this is a place to see what happened, not a copy of the data.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel
from substrate.runtime import RuntimeStore
from substrate.types import RunLogKind

MAX_RUNS = 100
SNIPPET = 400


class ToolStep(BaseModel):
    call_id: str = ""
    name: str
    args: str = ""
    ok: bool | None = None  # None: no result was journalled (the run stopped before it came back)
    output: str = ""
    risk: str | None = None


class RunDetail(BaseModel):
    run_id: str
    status: str
    started_at: datetime | None = None
    duration_ms: int | None = None
    model: str | None = None
    llm_calls: int = 0
    tokens: int = 0
    cost_usd: float = 0.0
    tools: list[ToolStep] = []
    error: str | None = None
    user_message: str = ""


def _cut(text: Any) -> str:
    s = text if isinstance(text, str) else repr(text)
    return s if len(s) <= SNIPPET else s[:SNIPPET] + "…"


def _args(args: Any) -> str:
    import json

    try:
        return _cut(json.dumps(args, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return _cut(args)


async def inspect_thread(store: RuntimeStore, thread_id: str) -> list[RunDetail]:
    """The thread's most recent runs, oldest first."""
    runs = (await store.find_runs(thread_id=thread_id, active_only=False))[-MAX_RUNS:]
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
        for entry in await store.read_events(run.run_id):
            p = entry.payload or {}
            if entry.kind == RunLogKind.USER_MESSAGE and not detail.user_message:
                detail.user_message = _cut(p.get("text", ""))
            elif entry.kind == RunLogKind.LLM_CALL:
                detail.llm_calls += 1
                detail.model = str(p.get("model") or detail.model or "") or None
                try:
                    detail.tokens += int(p.get("tokens") or 0)
                    detail.cost_usd += float(p.get("cost_usd") or 0.0)
                except (TypeError, ValueError):
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


class LastWord(BaseModel):
    """The last thing said in a conversation: a one-line preview, and when that run ended (or began, while it is still going)."""

    text: str
    at: datetime | None = None


async def last_message(store: RuntimeStore, thread_id: str, *, limit: int = 140) -> LastWord | None:
    """The last thing said in a conversation, as a one-line preview: the assistant's latest answer, else the user's latest message."""
    runs = await store.find_runs(thread_id=thread_id, active_only=False)
    for run in reversed(runs):
        said = {RunLogKind.ASSISTANT_MESSAGE: "", RunLogKind.USER_MESSAGE: ""}
        for entry in await store.read_events(run.run_id, durable_only=True):
            if entry.kind in said:
                text = str((entry.payload or {}).get("text") or "").strip()
                if text:
                    said[entry.kind] = text
        text = said[RunLogKind.ASSISTANT_MESSAGE] or said[RunLogKind.USER_MESSAGE]
        if text:
            line = " ".join(text.split())
            return LastWord(text=line if len(line) <= limit else line[:limit] + "…", at=run.terminated_at or run.started_at or run.enqueued_at)
    return None


__all__ = ["LastWord", "RunDetail", "ToolStep", "inspect_thread", "last_message"]
