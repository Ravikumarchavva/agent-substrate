"""UserProxyAgent — durable HITL bridge between humans and the agent runtime.

The proxy receives messages FROM other agents (HITL clarification requests)
and suspends via ``ctx.sleep_until_signal`` until a human provides input
(delivered via the HTTP layer → ``SignalBus.signal()``).

External callers that want to START a task should submit a Message directly
to a ``ReActAgent`` via ``Runtime.submit()``.
"""

from __future__ import annotations

from ravi.kernel.core.identity import AgentId
from ravi.kernel.messaging.message import ChatPayload, Message

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ravi.agents.runtime.context import RunContext


class UserProxyAgent:
    """HITL agent.

    When another agent sends a message with ``reply_to`` set (via
    ``ctx.ask``), this proxy suspends until a human sends a reply signal
    ``human_reply:<correlation_id>`` via the signal bus.

    The serving layer is responsible for:
    1. Surfacing the question to the human (via SSE or notification).
    2. Calling ``SignalBus.signal(run_id, "human_reply:<cid>", {text: ...})``
       when the human replies.

    Parameters
    ----------
    name:
        Routing key.
    key:
        Instance key — allows multiple proxy instances (e.g. per user).
    """

    def __init__(self, name: str = "proxy", *, key: str = "default") -> None:
        self.id = AgentId(type="proxy", key=key)
        self.model = None  # no LLM needed
        self.tools = None  # no tools needed

    # ------------------------------------------------------------------
    # Agent contract
    # ------------------------------------------------------------------

    async def run(self, ctx: RunContext, inbox: list[Message]) -> None:
        for msg in inbox:
            ctx.check()
            await self._handle_message(ctx, msg)

    # ------------------------------------------------------------------
    # HITL suspend/resume
    # ------------------------------------------------------------------

    async def _handle_message(self, ctx: RunContext, msg: Message) -> None:
        cid = msg.correlation_id

        # Emit the question to the event log so the serving layer can surface it
        question_text = self._extract_text(msg)
        await ctx._log(
            "hitl.question",
            {"correlation_id": cid, "text": question_text},
        )

        # Suspend until a human provides a reply
        signal_name = f"human_reply:{cid}"
        human_payload = await ctx.sleep_until_signal(signal_name)

        # Deliver human reply back to the asker
        if msg.reply_to:
            await ctx.reply(msg, human_payload)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _extract_text(self, msg: Message) -> str:
        payload = msg.payload
        if isinstance(payload, ChatPayload):
            from ravi.kernel.core.content import content_blocks_to_str
            return content_blocks_to_str(payload.message.content)  # type: ignore[arg-type]
        return str(getattr(payload, "data", payload))


__all__ = ["UserProxyAgent"]
