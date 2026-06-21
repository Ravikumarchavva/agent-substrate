"""InformationAgent — producer that summarizes source content and emits to a topic.

Use cases: YouTube channel monitor, RSS feed processor, Twitter/X list watcher,
Facebook page aggregator, etc.  Any source that produces items to be summarized
and fan-out to subscribers.

Lifecycle:
  1. Boot with a source item (or trigger signal).
  2. Summarize the item via ctx.llm().
  3. Emit the summary to ``output_topic`` (fan-out to all PersonalFeedAgent followers).
  4. Sleep until the next item arrives via the "new_source_item" signal.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from substrate.kernel.core.content import content_blocks_to_str
from substrate.kernel.core.identity import AgentId, TopicId
from substrate.kernel.messaging.message import ChatPayload, DataPayload, Message

from substrate.agents.core._loop import summarize

if TYPE_CHECKING:
    from substrate.agents.runtime.context import RunContext
    from substrate.kernel.llm.llm import LLMClient


class InformationAgent:
    """Producer agent.

    Parameters
    ----------
    name:
        Routing key (e.g. "youtube-monitor", "rss-techcrunch").
    model:
        LLMClient for summarization.
    output_topic:
        TopicId to emit summaries to.  Followers (PersonalFeedAgents) wake
        on delivery.
    system_instructions:
        System prompt for the summarization step.
    source_signal:
        Signal name that wakes this agent when a new item is available.
        Default: "new_source_item".
    """

    def __init__(
        self,
        name: str,
        *,
        model: LLMClient,
        output_topic: TopicId,
        system_instructions: str = "Summarize the following content concisely.",
        source_signal: str = "new_source_item",
    ) -> None:
        self.id = AgentId(type="agent", key=name)
        self.model = model
        self.tools = None  # producer doesn't need tools
        self._output_topic = output_topic
        self._system_instructions = system_instructions
        self._source_signal = source_signal

    # ------------------------------------------------------------------
    # Agent contract
    # ------------------------------------------------------------------

    async def run(self, ctx: RunContext, inbox: list[Message]) -> None:
        # Process any items already in the inbox (e.g. boot message with content)
        for msg in inbox:
            ctx.check()
            await self._process_item(ctx, msg)

        # Then go dormant — wake up when a new source item signal arrives
        while True:
            ctx.check()
            item_payload = await ctx.sleep_until_signal(self._source_signal)
            await self._process_item_from_signal(ctx, item_payload)

    # ------------------------------------------------------------------
    # Processing
    # ------------------------------------------------------------------

    async def _process_item(self, ctx: RunContext, msg: Message) -> None:
        """Summarize the content in an inbox message and emit to the topic."""
        raw_text = self._extract_text(msg)
        summary_text = await summarize(
            ctx, raw_text, instructions=self._system_instructions
        )
        await self._emit_summary(ctx, summary_text, source_id=msg.id)

    async def _process_item_from_signal(self, ctx: RunContext, payload: dict) -> None:
        """Summarize content delivered via a signal payload."""
        raw_text = payload.get("text", str(payload))
        summary_text = await summarize(
            ctx, raw_text, instructions=self._system_instructions
        )
        await self._emit_summary(
            ctx, summary_text, source_id=payload.get("source_id", "")
        )

    async def _emit_summary(
        self, ctx: RunContext, summary: str, *, source_id: str
    ) -> None:
        """Fan-out the summary to all followers of output_topic."""
        out_msg = Message(
            target=self._output_topic,
            sender=self.id,
            payload=DataPayload(data={"text": summary, "source_id": source_id}),
        )
        await ctx.emit(self._output_topic, out_msg)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _extract_text(self, msg: Message) -> str:
        payload = msg.payload
        if isinstance(payload, ChatPayload):
            return content_blocks_to_str(payload.message.content)  # type: ignore[arg-type]
        if isinstance(payload, DataPayload):
            return payload.data.get("text", str(payload.data))
        return str(payload)


__all__ = ["InformationAgent"]
