"""RunStreamAdapter — adapts a Agent + Runtime to the stream interface.

Presents the ``run_stream()`` method expected by ``AgentStreamSession`` while
internally using the submit + EventLog.tail() pattern:

  1. Build a ChatPayload Message from ``input_text``
  2. ``runtime.submit(agent_id, msg)`` → ``run_id``
  3. Tail ``event_log.tail(run_id)`` until ``run.completed`` or ``run.failed``
  4. Yield kernel stream events mapped from log entries

Token-level streaming: ``RunContext.llm()`` can be extended to emit
``TextDelta`` log entries during generation.  The mapper below handles them
if present; otherwise a single ``CompletionEvent`` is emitted at the end of
the run.
"""

from __future__ import annotations

import uuid
from typing import AsyncIterator, Any

from ravi.kernel.core.content import TextBlock
from ravi.kernel.core.identity import AgentId
from ravi.kernel.messaging.message import ChatPayload, Message
from ravi.kernel.messaging.stream import (
    AgentProgress,
    AgentStep,
    CompletionEvent,
    StreamDone,
    TextDelta,
)
from ravi.kernel.core.content import ChatMessage, Role


class RunStreamAdapter:
    """Adapts a Agent to the run_stream() interface used by AgentStreamSession.

    Parameters
    ----------
    agent_id:
        The registered agent's ID.
    runtime:
        The Runtime instance (must be started).
    tools:
        Flat list of tool objects — used by _build_tool_meta_map() in the route.
        Pass the tools list from the registered ReActAgent.
    """

    def __init__(
        self,
        *,
        agent_id: AgentId,
        runtime: Any,
        tools: list = (),
        correlation_id: str | None = None,
    ) -> None:
        self._agent_id = agent_id
        self._runtime = runtime
        self.tools = list(tools)
        self._correlation_id = correlation_id or uuid.uuid4().hex

    async def run_stream(
        self,
        input_text: str,
        *,
        initial_tool_choice: str | None = None,
        **_kwargs: Any,
    ) -> AsyncIterator[Any]:
        return self._stream(input_text)

    async def _stream(self, input_text: str) -> AsyncIterator[Any]:
        msg = Message(
            target=self._agent_id,
            sender=AgentId(type="proxy", key="http"),
            payload=ChatPayload(
                message=ChatMessage(
                    role=Role.USER,
                    content=[TextBlock(text=input_text)],
                )
            ),
            correlation_id=self._correlation_id,
        )

        run_id = await self._runtime.submit(self._agent_id, msg)
        event_log = self._runtime.event_log

        final_text = ""

        async for entry in event_log.tail(run_id):
            kind = entry.kind
            payload = entry.payload or {}

            if kind == "text.delta":
                # Emitted by a future ctx.llm_stream() implementation
                delta = payload.get("delta", "")
                final_text += delta
                yield TextDelta(text=delta)

            elif kind == "llm.call":
                # ctx.llm() stores the complete response — emit as text delta if
                # we haven't already accumulated token-by-token
                pass  # CompletionEvent emitted at run.completed

            elif kind == "tool.call":
                name = payload.get("name", "tool")
                status = payload.get("status", "ok")
                yield AgentProgress(
                    agent_id=self._agent_id,
                    step=AgentStep.TOOL_RESULT,
                    content=f"Tool {name} status: {status}",
                    run_id=run_id,
                    metadata={"tool_name": name, "status": status},
                )

            elif kind == "feed.curated":
                curated = payload.get("curated", "")
                if curated:
                    yield TextDelta(text=curated)
                    final_text += curated

            elif kind == "run.completed":
                # If no text accumulated via deltas, look at final history
                if not final_text:
                    final_text = await self._read_final_text(run_id, event_log)
                    if final_text:
                        yield TextDelta(text=final_text)
                yield CompletionEvent(
                    content=[TextBlock(text=final_text)],
                    metadata={"finish_reason": "stop"},
                )
                yield StreamDone(reason="success")
                return

            elif kind == "run.failed":
                error = payload.get("error", "Agent run failed")
                yield StreamDone(reason="error")
                raise RuntimeError(error)

            elif kind == "run.cancelled":
                yield StreamDone(reason="cancelled")
                return

    async def _read_final_text(self, run_id: str, event_log: Any) -> str:
        """Scan the event log for any text content accumulated during the run."""
        accumulated = []
        async for entry in event_log.read(run_id):
            p = entry.payload or {}
            if entry.kind == "text.delta":
                accumulated.append(p.get("delta", ""))
            elif entry.kind == "llm.response":
                accumulated.append(p.get("text", ""))
        
        final_log_text = "".join(accumulated)
        if not final_log_text:
            agent = self._runtime._registry.get(self._agent_id)
            if agent is not None:
                history = getattr(agent, "_context", None)
                if history is not None and hasattr(history, "history"):
                    session_id = self._correlation_id or self._agent_id.key
                    history_messages = await history.history.get_messages(
                        self._agent_id, session_id=session_id
                    )
                    from ravi.kernel.core.content import Role, content_blocks_to_str
                    for m in reversed(history_messages):
                        if m.role == Role.ASSISTANT:
                            return content_blocks_to_str(m.content)
        return final_log_text
