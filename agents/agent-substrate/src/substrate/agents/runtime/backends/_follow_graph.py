"""InMemoryFollowGraph — Stage 0 in-process implementation of FollowGraph."""

from __future__ import annotations

from collections import defaultdict
from typing import AsyncIterator

from substrate.kernel.core.identity import AgentId, TopicId
from substrate.kernel.messaging.message import Subscription


class InMemoryFollowGraph:
    """Single-process in-memory FollowGraph.

    ``_followers``: topic_key → set of AgentId that follow it.
    ``_following``: AgentId → set of topic_keys the agent follows.
    topic_key = ``"{type}/{source}"`` (matches TopicId fields).
    """

    def __init__(self) -> None:
        self._followers: dict[str, set[AgentId]] = defaultdict(set)
        self._following: dict[AgentId, set[str]] = defaultdict(set)

    @staticmethod
    def _key(topic: TopicId) -> str:
        return f"{topic.type}/{topic.source}"

    async def follow(self, follower: AgentId, topic: TopicId) -> Subscription:
        key = self._key(topic)
        self._followers[key].add(follower)
        self._following[follower].add(key)
        return Subscription(topic=topic, agent_id=follower)

    async def unfollow(self, sub: Subscription) -> None:
        key = self._key(sub.topic)
        self._followers[key].discard(sub.agent_id)
        self._following[sub.agent_id].discard(key)

    def followers_of(self, topic: TopicId) -> AsyncIterator[AgentId]:
        return self._followers_iter(topic)

    async def _followers_iter(self, topic: TopicId) -> AsyncIterator[AgentId]:  # type: ignore[return]
        for agent_id in list(self._followers[self._key(topic)]):
            yield agent_id

    def following(self, agent: AgentId) -> AsyncIterator[TopicId]:
        return self._following_iter(agent)

    async def _following_iter(self, agent: AgentId) -> AsyncIterator[TopicId]:  # type: ignore[return]
        for key in list(self._following[agent]):
            t, s = key.split("/", 1)
            yield TopicId(type=t, source=s)
