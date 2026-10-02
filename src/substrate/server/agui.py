"""AG-UI — the Agent-User Interaction protocol (https://docs.ag-ui.com), spoken by CopilotKit and other agent front ends.

``POST /agui`` takes a ``RunAgentInput`` and answers with a Server-Sent Events stream of AG-UI events::

    RUN_STARTED
      TEXT_MESSAGE_START / TEXT_MESSAGE_CONTENT… / TEXT_MESSAGE_END       one assistant message per model turn
      TOOL_CALL_START / TOOL_CALL_ARGS / TOOL_CALL_END / TOOL_CALL_RESULT
      CUSTOM {name: "substrate.approval_requested" | "substrate.input_requested" | "substrate.handoff"}
    RUN_FINISHED | RUN_ERROR

What maps and what does not:

* Conversation state lives on the server, in the engine's thread (``threadId``): only the **last user message** of the
  request is the prompt. A client that resends the whole transcript every turn is fine; it is not replayed.
* Frontend-defined ``tools``, ``state`` and ``context`` are accepted and ignored: the agent's tools are the server's.
* A tool that needs a person's approval suspends the run, durably. The stream announces it with a ``CUSTOM`` event and
  stays open; the decision is ``POST /runs/{runId}/approvals/{requestId}``, and the stream carries on.
* Reasoning tokens are not forwarded.
* ``runId`` is echoed as the client sent it; the engine's own run id is the ``X-Run-Id`` response header and the
  ``substrateRunId`` of the ``CUSTOM`` events — what the ``/runs/{runId}/…`` routes take.

``translate`` is a pure function from the engine's run-log entries to AG-UI events, so it is tested without a server.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from substrate.types import RunLogEntry, RunLogKind


class _Camel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore")


class AguiMessage(_Camel):
    id: str = ""
    role: str
    content: Any = None


class RunAgentInput(_Camel):
    """The request body AG-UI clients send."""

    thread_id: str
    run_id: str = ""
    messages: list[AguiMessage] = Field(default_factory=list)
    tools: list[Any] = Field(default_factory=list)
    context: list[Any] = Field(default_factory=list)
    state: Any = None
    forwarded_props: Any = None

    def prompt(self) -> str | None:
        """The text of the last user message, or ``None`` if there is none."""
        for message in reversed(self.messages):
            if message.role != "user":
                continue
            content = message.content
            if isinstance(content, str):
                return content
            if isinstance(content, list):  # multimodal: the text parts
                return "".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")
            return None
        return None


def encode(event: dict[str, Any]) -> str:
    """One AG-UI event as an SSE frame."""
    return f"data: {json.dumps(event, separators=(',', ':'), default=str)}\n\n"


class Translator:
    """Turns one run's log entries into AG-UI events. Stateful only in what AG-UI itself is: an open message."""

    def __init__(self, *, thread_id: str, run_id: str, engine_run_id: str | None = None) -> None:
        """``run_id`` is the id the client sent (AG-UI echoes it); ``engine_run_id`` is the engine's, which the approval
        routes need and which is carried in the ``CUSTOM`` events as ``substrateRunId``."""
        self._thread = thread_id
        self._run = run_id
        self._engine_run = engine_run_id or run_id
        self._message: str | None = None  # id of the assistant message currently open
        self._streamed = False  # whether this model turn's text has arrived as deltas
        self._turn = 0

    def start(self) -> list[dict[str, Any]]:
        return [{"type": "RUN_STARTED", "threadId": self._thread, "runId": self._run}]

    def _open(self) -> list[dict[str, Any]]:
        if self._message is not None:
            return []
        self._turn += 1
        self._message = f"{self._run}-m{self._turn}"
        return [{"type": "TEXT_MESSAGE_START", "messageId": self._message, "role": "assistant"}]

    def _close(self) -> list[dict[str, Any]]:
        if self._message is None:
            return []
        message, self._message = self._message, None
        return [{"type": "TEXT_MESSAGE_END", "messageId": message}]

    def translate(self, entry: RunLogEntry) -> list[dict[str, Any]]:
        kind, payload = entry.kind, entry.payload or {}
        if kind == RunLogKind.TEXT_DELTA:
            text = payload.get("text", "")
            if not text:
                return []
            self._streamed = True
            return [*self._open(), {"type": "TEXT_MESSAGE_CONTENT", "messageId": self._message, "delta": text}]
        if kind == RunLogKind.ASSISTANT_MESSAGE:
            events: list[dict[str, Any]] = []
            text = payload.get("text", "")
            if text and not self._streamed:  # a model that does not stream: the whole reply is the message
                events += [*self._open(), {"type": "TEXT_MESSAGE_CONTENT", "messageId": self._message, "delta": text}]
            self._streamed = False
            return [*events, *self._close()]
        if kind == RunLogKind.TOOL_CALL:
            call_id = payload.get("call_id") or f"{self._run}-t{entry.seq}"
            events = self._close()
            events.append({"type": "TOOL_CALL_START", "toolCallId": call_id, "toolCallName": payload.get("tool_name", "")})
            events.append({"type": "TOOL_CALL_ARGS", "toolCallId": call_id, "delta": json.dumps(payload.get("args", {}), default=str)})
            events.append({"type": "TOOL_CALL_END", "toolCallId": call_id})
            return events
        if kind == RunLogKind.TOOL_RESULT:
            call_id = payload.get("call_id") or f"{self._run}-t{entry.seq}"
            content = payload.get("output", "") if payload.get("ok", True) else f"error: {payload.get('error') or payload.get('output', '')}"
            return [{"type": "TOOL_CALL_RESULT", "messageId": f"{self._run}-r{entry.seq}", "toolCallId": call_id, "content": content, "role": "tool"}]
        if kind == RunLogKind.APPROVAL_REQUESTED:
            return [{"type": "CUSTOM", "name": "substrate.approval_requested", "value": {"substrateRunId": self._engine_run, **payload}}]
        if kind == RunLogKind.INPUT_REQUESTED:
            return [{"type": "CUSTOM", "name": "substrate.input_requested", "value": {"substrateRunId": self._engine_run, **payload}}]
        if kind == RunLogKind.SUBAGENT_START:
            return [{"type": "STEP_STARTED", "stepName": str(payload.get("agent") or payload.get("name") or "subagent")}]
        if kind == RunLogKind.SUBAGENT_DONE:
            return [{"type": "STEP_FINISHED", "stepName": str(payload.get("agent") or payload.get("name") or "subagent")}]
        if kind == RunLogKind.RUN_COMPLETED:
            return [*self._close(), {"type": "RUN_FINISHED", "threadId": self._thread, "runId": self._run}]
        if kind == RunLogKind.RUN_FAILED:
            return [*self._close(), {"type": "RUN_ERROR", "message": payload.get("error") or "agent run failed", "code": "run_failed"}]
        if kind == RunLogKind.RUN_CANCELLED:
            return [*self._close(), {"type": "RUN_ERROR", "message": "run cancelled", "code": "run_cancelled"}]
        return []

    def translate_all(self, entries: Iterable[RunLogEntry]) -> list[dict[str, Any]]:
        return [event for entry in entries for event in self.translate(entry)]


__all__ = ["AguiMessage", "RunAgentInput", "Translator", "encode"]
