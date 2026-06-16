"""Example 6-1: Local in-process Runtime — public API walkthrough.

Demonstrates:
  • Registering agents on the Runtime
  • Basic point-to-point submission using runtime.submit()
  • Pub/sub using runtime.follow() and runtime.publish()
"""

from __future__ import annotations

import asyncio

from ravi.kernel.core.identity import AgentId, TopicId
from ravi.kernel.messaging.message import Message, DataPayload
from ravi.agents.runtime import Runtime, RunContext


# --- Section 1: define custom agent types satisfying Agent protocol ---

class EchoAgent:
    """Returns the input text with a prefix."""
    def __init__(self, agent_id: AgentId) -> None:
        self.id = agent_id

    async def run(self, ctx: RunContext, inbox: list[Message]) -> None:
        for msg in inbox:
            text = msg.payload.data.get("text", "") if isinstance(msg.payload, DataPayload) else ""
            if msg.reply_to:
                await ctx.reply(msg, {"echo": text})


class EventLogAgent:
    """Records all topic events it receives."""
    def __init__(self, agent_id: AgentId) -> None:
        self.id = agent_id
        self.received: list[str] = []

    async def run(self, ctx: RunContext, inbox: list[Message]) -> None:
        for msg in inbox:
            text = msg.payload.data.get("text", "") if isinstance(msg.payload, DataPayload) else ""
            self.received.append(text)


async def main() -> None:
    async with Runtime() as rt:
        # --- Section 2: point-to-point messaging ---
        print("=== 1. Point-to-point submit ===")
        echo_id = AgentId(type="echo", key="demo")
        echo_agent = EchoAgent(echo_id)
        await rt.register(echo_agent)

        msg = Message(target=echo_id, payload=DataPayload(data={"text": "hello runtime"}))
        run_id = await rt.submit(echo_id, msg)
        print(f"  Submitted message to echo_agent (run_id: {run_id})")

        # Wait for run to complete by tailing log
        async for entry in rt.event_log.tail(run_id):
            if entry.kind in ("run.completed", "run.failed", "run.cancelled"):
                break
        print("  Run completed.")

        # --- Section 3: pub/sub via TopicId ---
        print("\n=== 2. Pub/sub publish ===")
        topic_type = "events"
        topic_source = "demo"
        
        listener_id = AgentId(type="listener", key="demo")
        listener_agent = EventLogAgent(listener_id)
        await rt.register(listener_agent)

        # Follow the topic
        await rt.follow(listener_id, topic_type, topic_source)

        pub_msg = Message(target=TopicId(type=topic_type, source=topic_source), payload=DataPayload(data={"text": "broadcast event"}))
        await rt.publish(topic_type, topic_source, pub_msg)

        # Give it a tiny moment to process the queue
        await asyncio.sleep(0.1)
        print(f"  listener_agent received events: {listener_agent.received}")


if __name__ == "__main__":
    asyncio.run(main())
