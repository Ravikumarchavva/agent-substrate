"""Local in-process AgentRuntime — public API walkthrough.

Shows how ``LocalRuntime`` differs from a bare asyncio function call:
mailbox isolation, supervised restart, location-transparent addressing,
and topic pub/sub — all using only the public API.

Infrastructure: none (pure asyncio, no external services).
"""

from __future__ import annotations

import asyncio

from ravi.kernel.runtime import (
    AgentId,
    LocalRuntime,
    RestartPolicy,
    TopicId,
)
from ravi.kernel.runtime._contracts import MessageContext
from ravi.kernel.messages.content import ContentBlock
from ravi.extensions.agents.runtime.agent import RuntimeAgent

# ---
# What LocalRuntime adds over plain asyncio functions:
#
#   1. Each agent gets its own bounded mailbox (asyncio.Queue) — the caller
#      never blocks the agent's internal processing.
#   2. The Supervisor restarts crashed agents up to a configurable budget.
#   3. Agents are addressed by AgentId(type, key) — the caller does not hold
#      a direct reference; routing is transparent and swappable (local →
#      gRPC → distributed) without changing call sites.
#   4. Pub/sub via TopicId lets many agents receive one broadcast without
#      point-to-point wiring.
# ---


# --- Section 1: define custom agent types via RuntimeAgent ---


class EchoAgent(RuntimeAgent):
    """Returns the input text with a prefix."""

    async def on_message(
        self, ctx: MessageContext, content: list[ContentBlock]
    ) -> object:
        text = content[0].text if content and hasattr(content[0], "text") else ""
        return f"[echo] {text}"


class UpperAgent(RuntimeAgent):
    """Uppercases the input text."""

    async def on_message(
        self, ctx: MessageContext, content: list[ContentBlock]
    ) -> object:
        text = content[0].text if content and hasattr(content[0], "text") else ""
        return text.upper()


class EventLogAgent(RuntimeAgent):
    """Records all topic events it receives."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.received: list[str] = []

    async def on_message(
        self, ctx: MessageContext, content: list[ContentBlock]
    ) -> object:
        text = content[0].text if content and hasattr(content[0], "text") else ""
        self.received.append(text)
        return None


# --- Section 2: basic point-to-point messaging ---


async def demo_point_to_point(runtime: LocalRuntime) -> None:
    print("=== Point-to-point send_message ===")

    echo = EchoAgent(name="echo", runtime=runtime)
    upper = UpperAgent(name="upper", runtime=runtime)
    await echo.start()
    await upper.start()

    result = await runtime.send_message("hello world", recipient=echo.id)
    print(f"  echo response:  {result!r}")

    result = await runtime.send_message("hello world", recipient=upper.id)
    print(f"  upper response: {result!r}")

    # Chained call: route via sender identity
    chain_result = await runtime.send_message(
        "chained call",
        sender=echo.id,
        recipient=upper.id,
    )
    print(f"  chained result: {chain_result!r}")

    await echo.stop()
    await upper.stop()


# --- Section 3: pub/sub via TopicId ---


async def demo_pub_sub(runtime: LocalRuntime) -> None:
    print("\n=== Pub/sub publish_message ===")

    broadcast_topic = TopicId(type="events", source="demo")

    # Two subscriber agents, both subscribe to the same topic
    listener_a = EventLogAgent(
        name="listener_a",
        runtime=runtime,
        subscriptions=[broadcast_topic],
    )
    listener_b = EventLogAgent(
        name="listener_b",
        runtime=runtime,
        subscriptions=[broadcast_topic],
    )
    await listener_a.start()
    await listener_b.start()

    # Publish once — both agents receive the same message
    await runtime.publish_message("broadcast event #1", topic=broadcast_topic)
    await runtime.publish_message("broadcast event #2", topic=broadcast_topic)

    # Give the event loop a tick to drain mailboxes before reading results
    await asyncio.sleep(0.05)

    print(f"  listener_a received: {listener_a.received}")
    print(f"  listener_b received: {listener_b.received}")

    await listener_a.stop()
    await listener_b.stop()


# --- Section 4: supervisor restart on crash ---


class FlakyAgent(RuntimeAgent):
    """Crashes on the first call, succeeds on subsequent calls."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._call_count = 0

    async def on_message(
        self, ctx: MessageContext, content: list[ContentBlock]
    ) -> object:
        self._call_count += 1
        if self._call_count == 1:
            raise RuntimeError("intentional crash on first call")
        return f"recovered after crash (call #{self._call_count})"


async def demo_supervisor(runtime: LocalRuntime) -> None:
    print("\n=== Supervisor restart on crash ===")

    flaky = FlakyAgent(name="flaky", runtime=runtime)
    await flaky.start()

    # First message — the handler crashes; the supervisor restarts the agent
    try:
        await runtime.send_message("trigger crash", recipient=flaky.id)
    except Exception as exc:
        print(f"  first call raised (expected): {type(exc).__name__}: {exc}")

    # Second message — agent is alive again after restart
    try:
        result = await runtime.send_message("after restart", recipient=flaky.id)
        print(f"  second call succeeded: {result!r}")
    except Exception as exc:
        print(f"  second call raised: {type(exc).__name__}: {exc}")

    await flaky.stop()


# --- Section 5: runtime introspection via public properties ---


async def demo_introspection(runtime: LocalRuntime) -> None:
    print("\n=== Runtime introspection (public properties only) ===")

    agent = EchoAgent(name="echo_inspect", runtime=runtime)
    await agent.start()

    print(f"  worker_id:        {runtime.worker_id}")
    print(f"  registered_types: {runtime.registered_types}")
    print(f"  lease_registry:   {runtime.lease_registry!r}  (None = single-worker mode)")
    print(f"  resource_locks:   {runtime.resource_locks!r}")
    print(f"  saga_coordinator: {runtime.saga_coordinator!r}")

    await agent.stop()


async def main() -> None:
    # RestartPolicy: up to 5 restarts within a 60-second window
    restart_policy = RestartPolicy(max_restarts=5, restart_window=60.0)
    runtime = LocalRuntime(restart_policy=restart_policy, send_timeout=10.0)
    await runtime.start()

    try:
        await demo_point_to_point(runtime)
        await demo_pub_sub(runtime)
        await demo_supervisor(runtime)
        await demo_introspection(runtime)
    finally:
        await runtime.stop()
        print("\nRuntime stopped cleanly.")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
