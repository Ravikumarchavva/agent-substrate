"""PushAllFanout — Stage 0 push-all implementation of FanoutStrategy."""

from __future__ import annotations

from ravi.kernel.core.identity import TopicId
from ravi.kernel.messaging.message import Message
from ravi.kernel.runtime.follow_graph import FollowGraph
from ravi.kernel.runtime.inbox import Inbox


class PushAllFanout:
    """Delivers to every follower synchronously (Stage 0 / Stage 1).

    Stage 3 replaces this with a push/pull hybrid for high-follower-count
    topics (celebrity-agent problem) — the swap is behind the FanoutStrategy
    Protocol so no caller changes.
    """

    async def publish(
        self,
        topic: TopicId,
        msg: Message,
        *,
        graph: FollowGraph,
        inbox: Inbox,
    ) -> None:
        async for follower in graph.followers_of(topic):
            await inbox.deliver(follower, msg)
