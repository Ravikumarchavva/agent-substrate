"""InMemoryFollowGraph — Stage 0 in-process implementation of FollowGraph."""

from __future__ import annotations

from collections import defaultdict
from typing import AsyncIterator

from substrate.kernel.core.identity import Actor, Topic
from substrate.kernel.messaging.message import Subscription


class InMemoryFollowGraph:
    """Single-process in-memory FollowGraph.

    ``_followers``: topic name → set of Actor that follow it.
    ``_following``: Actor → set of topic names the agent follows.
    """

    def __init__(self) -> None:
        self._followers: dict[str, set[Actor]] = defaultdict(set)
        self._following: dict[Actor, set[str]] = defaultdict(set)

    @staticmethod
    def _key(topic: Topic) -> str:
        return topic.name

    async def follow(self, follower: Actor, topic: Topic) -> Subscription:
        key = self._key(topic)
        self._followers[key].add(follower)
        self._following[follower].add(key)
        return Subscription(topic=topic, agent_id=follower)

    async def unfollow(self, sub: Subscription) -> None:
        key = self._key(sub.topic)
        self._followers[key].discard(sub.agent_id)
        self._following[sub.agent_id].discard(key)

    def followers_of(self, topic: Topic) -> AsyncIterator[Actor]:
        return self._followers_iter(topic)

    async def _followers_iter(self, topic: Topic) -> AsyncIterator[Actor]:  # type: ignore[return]
        for agent_id in list(self._followers[self._key(topic)]):
            yield agent_id

    def following(self, agent: Actor) -> AsyncIterator[Topic]:
        return self._following_iter(agent)

    async def _following_iter(self, agent: Actor) -> AsyncIterator[Topic]:  # type: ignore[return]
        for key in list(self._following[agent]):
            yield Topic(key)
