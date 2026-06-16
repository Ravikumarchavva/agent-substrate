"""PersonalFeedAgent — consumer that curates a personal feed.

Follows one or more InformationAgent topics via the FollowGraph + fan-out
mechanism.  Each fan-out delivery wakes this agent; it deduplicated, ranks,
and summarizes the new items against the user's preference history.

Use case proof: YouTube/Facebook/Twitter personal feed curation without polling.
  InformationAgent emits → FollowGraph fan-out → PersonalFeedAgent inbox delivery
  → ranking LLM call → curated feed entry stored / surfaced to user.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ravi.kernel.core.identity import AgentId, TopicId
from ravi.kernel.messaging.message import DataPayload, Message

from ravi.agents.core._loop import summarize

if TYPE_CHECKING:
    from ravi.agents.runtime.context import RunContext
    from ravi.kernel.llm.llm import LLMClient


class PersonalFeedAgent:
    """Consumer agent that curates a personal feed.

    Parameters
    ----------
    name:
        Routing key (e.g. "feed-user-42").
    model:
        LLMClient for ranking/summarization.
    follow_topics:
        Topics to subscribe to via the FollowGraph.  Fan-out deliveries
        from these topics will wake this agent.
    system_instructions:
        System prompt for the ranking/curation LLM call.
    preferences:
        Optional preference context injected into the ranking prompt.
        Can be updated between runs by callers.
    """

    def __init__(
        self,
        name: str,
        *,
        model: LLMClient,
        follow_topics: list[TopicId],
        system_instructions: str = (
            "You are a personal feed curator.  "
            "Given a new item and the user's preferences, "
            "rank it (high/medium/low relevance) and write a one-sentence summary."
        ),
        preferences: str = "",
    ) -> None:
        self.id = AgentId(type="agent", key=name)
        self.model = model
        self.tools = None
        self._follow_topics = follow_topics
        self._system_instructions = system_instructions
        self._preferences = preferences
        self._subscribed = False  # subscriptions survive across runs via FollowGraph

    # ------------------------------------------------------------------
    # Agent contract
    # ------------------------------------------------------------------

    async def run(self, ctx: RunContext, inbox: list[Message]) -> None:
        # Subscribe to followed topics on first wake (FollowGraph is durable)
        if not self._subscribed:
            for topic in self._follow_topics:
                await ctx.follow(topic)
            self._subscribed = True

        # Process delivered items
        for msg in inbox:
            ctx.check()
            await self._curate(ctx, msg)

        # Sleep until the next fan-out delivery
        await ctx.sleep_until_signal("new_feed_item")

    # ------------------------------------------------------------------
    # Curation
    # ------------------------------------------------------------------

    async def _curate(self, ctx: RunContext, msg: Message) -> None:
        """Rank and summarize one delivered item."""
        item_text = self._extract_text(msg)

        ranking_prompt = (
            f"User preferences: {self._preferences}\n\nNew item:\n{item_text}"
            if self._preferences
            else item_text
        )

        curated = await summarize(
            ctx, ranking_prompt, instructions=self._system_instructions
        )

        # Log the curated entry so it's visible via EventLog.tail()
        await ctx._log(
            "feed.curated",
            {
                "source_msg_id": msg.id,
                "curated": curated,
                "raw_text": item_text[:200],
            },
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _extract_text(self, msg: Message) -> str:
        payload = msg.payload
        if isinstance(payload, DataPayload):
            return payload.data.get("text", str(payload.data))
        return str(payload)


__all__ = ["PersonalFeedAgent"]
