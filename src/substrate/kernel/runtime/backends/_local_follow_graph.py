"""LocalFollowGraph — SQLite-durable FollowGraph (no-infra tier)."""

from __future__ import annotations

import sqlite3
from typing import AsyncIterator

from substrate.kernel.core.identity import Actor, Topic
from substrate.kernel.messaging.message import Subscription

from ._local_db import LocalRuntimeDB


class LocalFollowGraph:
    """SQLite-backed FollowGraph."""

    def __init__(self, db: LocalRuntimeDB) -> None:
        self._db = db

    async def follow(self, follower: Actor, topic: Topic) -> Subscription:
        topic_key, follower_key = topic.name, str(follower)

        def _do(conn: sqlite3.Connection) -> None:
            conn.execute(
                "INSERT OR IGNORE INTO follow_edges (topic, follower) VALUES (?, ?)",
                (topic_key, follower_key),
            )

        await self._db.run(_do)
        return Subscription(topic=topic, agent_id=follower)

    async def unfollow(self, sub: Subscription) -> None:
        topic_key, follower_key = sub.topic.name, str(sub.agent_id)

        def _do(conn: sqlite3.Connection) -> None:
            conn.execute(
                "DELETE FROM follow_edges WHERE topic = ? AND follower = ?",
                (topic_key, follower_key),
            )

        await self._db.run(_do)

    def followers_of(self, topic: Topic) -> AsyncIterator[Actor]:
        return self._followers_iter(topic)

    async def _followers_iter(self, topic: Topic) -> AsyncIterator[Actor]:  # type: ignore[return]
        for actor_str in await self._followers_of(topic):
            yield Actor.from_str(actor_str)

    async def _followers_of(self, topic: Topic) -> list[str]:
        def _do(conn: sqlite3.Connection) -> list[str]:
            rows = conn.execute(
                "SELECT follower FROM follow_edges WHERE topic = ?", (topic.name,)
            ).fetchall()
            return [r["follower"] for r in rows]

        return await self._db.run(_do)

    def following(self, agent: Actor) -> AsyncIterator[Topic]:
        return self._following_iter(agent)

    async def _following_iter(self, agent: Actor) -> AsyncIterator[Topic]:  # type: ignore[return]
        agent_str = str(agent)

        def _do(conn: sqlite3.Connection) -> list[str]:
            rows = conn.execute(
                "SELECT topic FROM follow_edges WHERE follower = ?", (agent_str,)
            ).fetchall()
            return [r["topic"] for r in rows]

        for key in await self._db.run(_do):
            yield Topic(key)


__all__ = ["LocalFollowGraph"]
