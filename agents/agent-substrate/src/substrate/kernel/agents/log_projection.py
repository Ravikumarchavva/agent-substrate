"""EventLog → step-row → ChatMessage projection.

Split out of ``factory.py`` (which used to bundle this with agent
construction and a compaction preset — three unrelated jobs in one file).
This is core-agent machinery: reconstructing the message list an LLM call
sees from durable history, the same job ``core/base.py``'s ``load_history``
does via ``storage.history.project_messages`` for the live DAG-history path
— this module is the cold-store equivalent (rebuild from persisted
step-rows/EventLogProtocol instead of the in-memory DAG).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from substrate.kernel.runtime.log_entry import RunLogKind
from substrate.kernel import (
    ChatMessage,
    ContentBlock,
    TextBlock,
    ToolUseBlock,
    ToolResultBlock,
)

if TYPE_CHECKING:
    from substrate.kernel.runtime.log_entry import EventLogProtocol
    from substrate.agents.runtime._scheduling import SchedulerBackend


async def rebuild_messages_from_steps(
    step_rows: list[dict],
    system_instructions: str,
    *,
    include_mcp_app_context: bool = False,
) -> list[ChatMessage]:
    """Rebuild framework messages from step-row dicts using unified ChatMessage.

    ``step_rows`` doesn't have to come from an actual ``steps`` database row —
    it's a plain schema (``type``/``input``/``output``/``generation``/
    ``metadata``/``name``) that any cold-store source can project into. The
    monolith projects it from the EventLogProtocol (see ``step_rows_from_log``); the
    microservices ``agent_runtime`` service projects it from the
    ``conversation`` service's own independent store via HTTP — both funnel
    through this one conversion so there's a single place that knows how a
    step-row maps to a ``ChatMessage``.
    """
    messages: list[ChatMessage] = []
    if system_instructions:
        messages.append(
            ChatMessage(
                role="system",
                content=[TextBlock(text=system_instructions)],
            )
        )

    for row in step_rows:
        step_type = row["type"]
        meta = row.get("metadata") or {}

        if step_type == "system_message":
            continue

        if step_type == "user_message":
            # `step_rows_from_log` already replaces `input` with a
            # placeholder for any message a safety guardrail flagged (see
            # that function's own redaction pass) — this branch has nothing
            # extra to do; the substitution already happened upstream, so
            # a flagged message's raw content never reaches this point.
            messages.append(
                ChatMessage(
                    role="user",
                    content=[TextBlock(text=row.get("input") or "")],
                )
            )
            continue

        if step_type == "assistant_message":
            content_blocks = []
            output_text = row.get("output")
            if output_text:
                content_blocks.append(TextBlock(text=output_text))

            generation = row.get("generation") or {}
            if generation.get("tool_calls"):
                for tool_call in generation["tool_calls"]:
                    call_id = tool_call.get("id") or tool_call.get("call_id") or ""
                    tool_name = (
                        tool_call.get("name") or tool_call.get("tool_name") or ""
                    )
                    args = tool_call.get("arguments") or {}
                    content_blocks.append(
                        ToolUseBlock(
                            call_id=call_id,
                            tool_name=tool_name,
                            arguments=args,
                        )
                    )

            messages.append(
                ChatMessage(
                    role="assistant",
                    content=content_blocks,
                )
            )
            continue

        if step_type == "tool_result":
            call_id = meta.get("tool_call_id") or ""
            tool_name = row.get("name") or ""
            output_text = row.get("output") or ""
            is_error = row.get("is_error") or False
            tool_content: list[ContentBlock] = [
                ToolResultBlock(
                    call_id=call_id,
                    name=tool_name,
                    content=[TextBlock(text=output_text)],
                    is_error=is_error,
                )
            ]
            messages.append(ChatMessage(role="tool", content=tool_content))
            continue

        if step_type == "tool_call":
            continue

        if step_type == RunLogKind.MCP_APP_CONTEXT and include_mcp_app_context:
            tool_name = row.get("name", "mcp_app")
            context_data = row.get("output") or ""
            context_msg = (
                f"[MCP App Update — {tool_name}] "
                f"The user interacted with the {tool_name} widget. "
                f"Current state:\n{context_data}"
            )
            messages.append(
                ChatMessage(
                    role="user",
                    content=[TextBlock(text=context_msg)],
                )
            )

    return messages


async def step_rows_from_log(
    event_log: EventLogProtocol, scheduler: SchedulerBackend, thread_id: str
) -> list[dict]:
    """Project a thread's EventLogProtocol into ``rebuild_messages_from_steps``'s
    step-row schema — the monolith's cold-store source, now that the EventLogProtocol
    (not a separate ``steps`` table) is the single source of truth for
    conversation history (see ``serving/stream/history.py::project_thread()``,
    the sibling projection for UI display).

    Turn-boundary rule matches ``project_thread``'s UI-facing counterpart
    (substrate-ui's ``history-fold.ts``): a ``text.delta``/``tool.call``
    arriving after a ``tool.result`` starts a new ``assistant_message`` row —
    each real LLM generation's text + tool-use calls land in one row, exactly
    matching how the live react loop actually shaped them.

    Redaction: any ``user_message`` row whose ``user.message`` entry was
    later flagged by a safety guardrail (a companion ``user.message.flagged``
    entry referencing its seq — see ``agents/middleware/guardrails/
    multimodal_safety.py``) has its ``input`` replaced with a fixed
    placeholder before this function returns. This is what makes
    "persist-but-exclude" real for *future* turns: the flagged message stays
    visible via the EventLogProtocol/wire-event history a client reads
    directly (``serving/stream/history.py::project_thread``, untouched by
    this function), but never re-enters the ``messages`` list an LLM call
    actually sees on any turn after the one it was flagged on. The marker
    always appears after its target within the same run (the guardrail logs
    it mid-turn, after the message it's flagging was already journaled), so
    a single pass — collect flagged seqs, redact at the end — is sufficient;
    no need to buffer or reorder anything.
    """
    _REDACTED_TEXT = "[Message removed — flagged for policy violation]"
    rows: list[dict] = []
    flagged_seqs: set[int] = set()
    current: dict | None = None
    saw_tool_result = False

    def _flush() -> None:
        nonlocal current, saw_tool_result
        if current is not None:
            rows.append(current)
        current = None
        saw_tool_result = False

    run_ids = await scheduler.find_all_runs_for_thread(thread_id)
    for run_id in run_ids:
        async for entry in event_log.read(run_id):
            kind = entry.kind
            payload = entry.payload or {}

            if kind == RunLogKind.USER_MESSAGE:
                _flush()
                rows.append(
                    {
                        "type": "user_message",
                        "input": payload.get("text", ""),
                        "metadata": {"seq": entry.seq},
                    }
                )
                continue

            if kind == RunLogKind.USER_MESSAGE_FLAGGED:
                # Not a message in the conversation itself — a marker
                # referencing one. No row of its own; redacted below.
                seq = payload.get("seq")
                if isinstance(seq, int):
                    flagged_seqs.add(seq)
                continue

            if kind == RunLogKind.TEXT_DELTA:
                if saw_tool_result:
                    _flush()
                if current is None:
                    current = {
                        "type": "assistant_message",
                        "output": "",
                        "generation": {},
                    }
                current["output"] = (current.get("output") or "") + payload.get(
                    "text", ""
                )
                continue

            if kind == RunLogKind.TOOL_CALL:
                if saw_tool_result:
                    _flush()
                if current is None:
                    current = {
                        "type": "assistant_message",
                        "output": "",
                        "generation": {},
                    }
                tool_calls = current.setdefault("generation", {}).setdefault(
                    "tool_calls", []
                )
                tool_calls.append(
                    {
                        "call_id": payload.get("call_id", ""),
                        "tool_name": payload.get("tool_name", ""),
                        "arguments": payload.get("args") or {},
                    }
                )
                continue

            if kind == RunLogKind.TOOL_RESULT:
                rows.append(
                    {
                        "type": "tool_result",
                        "name": payload.get("tool_name", ""),
                        "output": payload.get("output", ""),
                        "is_error": not payload.get("ok", True),
                        "metadata": {"tool_call_id": payload.get("call_id", "")},
                    }
                )
                saw_tool_result = True
                continue

            if kind == RunLogKind.MCP_APP_CONTEXT:
                _flush()
                rows.append(
                    {
                        "type": RunLogKind.MCP_APP_CONTEXT,
                        "name": payload.get("tool_name", "mcp_app"),
                        "output": payload.get("context", ""),
                    }
                )
                continue

    _flush()

    if flagged_seqs:
        for row in rows:
            if row.get("type") != "user_message":
                continue
            seq = (row.get("metadata") or {}).get("seq")
            if seq in flagged_seqs:
                row["input"] = _REDACTED_TEXT

    return rows


__all__ = ["rebuild_messages_from_steps", "step_rows_from_log"]
