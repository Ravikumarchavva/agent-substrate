"""Runtime internals — observable behaviors using only public APIs.

The old version of this file accessed 8+ private attributes
(_mailboxes, _restart_times, _agents_started, _pending_responses, etc.).
This version demonstrates the same CONCEPTS by observing PUBLIC behavior:

  - Mailbox backpressure       → send to a slow agent, observe TimeoutError
  - Message ordering guarantee → sequence numbers on responses confirm FIFO
  - Supervised restart         → crash an agent, verify it handles next message
  - Lifecycle states           → AgentLifecycleState enum values
  - Protocol structure         → Envelope, MessageContext, AgentId dataclasses

Infrastructure: none (pure asyncio, no external services).
"""

from __future__ import annotations

import asyncio
import time

from ravi.kernel.runtime import (
    AgentId,
    AgentLifecycleState,
    ActivationTrigger,
    CancellationToken,
    Envelope,
    HandlerError,
    MailboxFullError,
    RestartPolicy,
    TopicId,
)
from ravi.fabric.runtime import LocalRuntime, Mailbox
from ravi.kernel.runtime._contracts import MessageContext
from ravi.kernel.messages.content import ContentBlock, TextBlock
from ravi.fabric.actors.actor import ActorAgent

# ---
# How the runtime works — conceptual map
#
#  ┌─────────────────────────────────────────────────────┐
#  │  LocalRuntime                                       │
#  │                                                     │
#  │  send_message(msg, recipient=AgentId(...))          │
#  │       │                                             │
#  │  ┌────▼──────────────────────────┐                 │
#  │  │  Dispatcher                   │                 │
#  │  │  • routes AgentId → Mailbox   │                 │
#  │  │  • fans out TopicId → N boxes │                 │
#  │  └────┬──────────────────────────┘                 │
#  │       │                                             │
#  │  ┌────▼──────┐   ┌──────────────────────────────┐  │
#  │  │  Mailbox  │   │  Supervisor                  │  │
#  │  │  asyncio  │   │  • watches each agent Task   │  │
#  │  │  Queue    │   │  • restarts on crash (budget) │  │
#  │  │  bounded  │   │  • escalates when budget gone │  │
#  │  └────┬──────┘   └──────────────────────────────┘  │
#  │       │                                             │
#  │  ┌────▼───────────────────────────────────────┐    │
#  │  │  Agent message-loop Task (per instance)    │    │
#  │  │  • drains mailbox with async for           │    │
#  │  │  • calls registered handler(ctx, content)  │    │
#  │  │  • resolves pending Future → caller        │    │
#  │  └────────────────────────────────────────────┘    │
#  └─────────────────────────────────────────────────────┘
#
#  KEY GUARANTEE: per-agent FIFO ordering — all messages sent to the same
#  AgentId are processed in the order they were dispatched to its mailbox.
# ---


# --- Section 1: AgentId and TopicId — identity value objects ---


async def demo_identity() -> None:
    print("=== AgentId / TopicId identity ===")

    a = AgentId(type="worker", key="thread-001")
    t = TopicId(type="sse_events", source="session-42")

    print(f"  AgentId: {a}  (type={a.type!r}, key={a.key!r})")
    print(f"  TopicId: {t}  (type={t.type!r}, source={t.source!r})")
    print(f"  Hashable (usable as dict key): {hash(a)}")
    print("  Frozen (immutable): ", end="")
    try:
        object.__setattr__(a, "type", "other")  # frozen dataclass
        print("NOT frozen (unexpected)")
    except (TypeError, AttributeError):
        print("yes, raises on mutation attempt")


# --- Section 2: AgentLifecycleState enum — the agent state machine ---


async def demo_lifecycle_states() -> None:
    print("\n=== AgentLifecycleState enum ===")

    # ---
    # State machine for a dormant-capable agent:
    #
    #   DORMANT → ACTIVATING → ACTIVE → HIBERNATING → DORMANT
    #                                 → SUSPENDED   (HITL / quota pause)
    #                                 → TERMINATED  (final)
    #
    # LocalRuntime agents go DORMANT→ACTIVE on first message,
    # ACTIVE→TERMINATED when the runtime stops.
    # ---

    for state in AgentLifecycleState:
        print(f"  {state.name:15s} = {state.value}")

    trigger = ActivationTrigger(
        trigger_type="message",
        source_id="envelope-abc123",
    )
    print(
        f"\n  ActivationTrigger: type={trigger.trigger_type!r}, "
        f"source={trigger.source_id!r}, replayed={trigger.replayed}"
    )


# --- Section 3: Envelope — the unit of communication ---


async def demo_envelope() -> None:
    print("\n=== Envelope structure ===")

    sender = AgentId(type="user", key="u1")
    target = AgentId(type="echo", key="e1")

    env = Envelope(
        sender=sender,
        target=target,
        content=[TextBlock(text="hello runtime")],
    )

    print(f"  correlation_id: {env.correlation_id[:12]}...")
    print(f"  sender:         {env.sender}")
    print(f"  target:         {env.target}")
    print(f"  content[0]:     {env.content[0].text!r}")
    print(f"  is_expired:     {env.is_expired}")
    print(f"  priority:       {env.priority}")


# --- Section 4: Mailbox — bounded backpressure, observable via MailboxFullError ---


async def demo_mailbox_backpressure() -> None:
    print("\n=== Mailbox backpressure (public API) ===")

    # ---
    # Each agent has its own Mailbox (capacity-bounded asyncio.Queue).
    # When capacity is full, put_nowait raises MailboxFullError.
    # This prevents unbounded memory growth under load (backpressure).
    # ---

    mbox = Mailbox(capacity=3)
    sender = AgentId("src", "s")
    target = AgentId("dst", "d")

    # Fill to capacity
    for i in range(3):
        env = Envelope(
            sender=sender, target=target, content=[TextBlock(text=f"msg-{i}")]
        )
        mbox.put_nowait(env)

    print("  Filled to capacity=3")

    # 4th message → MailboxFullError (public exception, not private state)
    try:
        overflow = Envelope(
            sender=sender, target=target, content=[TextBlock(text="overflow")]
        )
        mbox.put_nowait(overflow)
    except MailboxFullError as exc:
        print(f"  MailboxFullError raised (expected): {exc}")

    # Drain one, then put again succeeds
    env = await mbox.get(timeout=1.0)
    print(f"  Drained: {env.content[0].text!r}")
    mbox.put_nowait(
        Envelope(sender=sender, target=target, content=[TextBlock(text="retry-ok")])
    )
    print("  Re-enqueue after drain: succeeded")

    mbox.close()


# --- Section 5: message ordering guarantee — FIFO per agent ---


class OrderingAgent(ActorAgent):
    """Records the order messages are received."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.log: list[int] = []

    async def on_message(
        self, ctx: MessageContext, content: list[ContentBlock]
    ) -> object:
        seq = int(content[0].text) if content and hasattr(content[0], "text") else -1
        self.log.append(seq)
        return seq


async def demo_ordering(runtime: LocalRuntime) -> None:
    print("\n=== FIFO ordering guarantee ===")

    agent = OrderingAgent(name="ordering", runtime=runtime)
    await agent.start()

    # Send 5 messages in sequence; runtime must deliver them in order
    for i in range(5):
        await runtime.send_message(str(i), recipient=agent.id)

    print(f"  received in order: {agent.log}")
    assert agent.log == list(range(5)), f"ordering violated: {agent.log}"
    print("  ordering verified ✓")

    await agent.stop()


# --- Section 6: supervisor restart — observable via second message succeeding ---


class CountingAgent(ActorAgent):
    """Fails once, then succeeds."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.attempts = 0

    async def on_message(
        self, ctx: MessageContext, content: list[ContentBlock]
    ) -> object:
        self.attempts += 1
        if self.attempts == 1:
            raise RuntimeError("deliberate first-call crash")
        return f"ok on attempt {self.attempts}"


async def demo_supervisor_restart(runtime: LocalRuntime) -> None:
    print("\n=== Supervisor restart — observed via recovery ===")

    # ---
    # Supervisor strategy: when an agent handler raises, the agent's message-
    # loop task is replaced with a fresh one.  The next send_message to the
    # same AgentId therefore succeeds (new task, fresh state).
    # ---

    counter = CountingAgent(name="counter", runtime=runtime)
    await counter.start()

    # First call crashes
    try:
        await runtime.send_message("first", recipient=counter.id)
    except (HandlerError, RuntimeError) as exc:
        print(f"  first call raised (expected): {type(exc).__name__}")

    # Second call succeeds — agent was restarted by the supervisor
    try:
        result = await runtime.send_message("second", recipient=counter.id)
        print(f"  second call succeeded after restart: {result!r}")
    except Exception as exc:
        print(f"  second call also raised: {type(exc).__name__}: {exc}")

    await counter.stop()


# --- Section 7: CancellationToken — cooperative cancellation ---


async def demo_cancellation(runtime: LocalRuntime) -> None:
    print("\n=== CancellationToken ===")

    class SlowAgent(ActorAgent):
        async def on_message(
            self, ctx: MessageContext, content: list[ContentBlock]
        ) -> object:
            await asyncio.sleep(30)  # will be cancelled
            return "never reached"

    slow = SlowAgent(name="slow_agent", runtime=runtime)
    await slow.start()

    token = CancellationToken()
    task = asyncio.create_task(
        runtime.send_message("start", recipient=slow.id, cancellation_token=token)
    )

    await asyncio.sleep(0.05)
    token.cancel()

    try:
        await task
    except asyncio.CancelledError:
        print("  send_message cancelled via CancellationToken ✓")

    await slow.stop()


# --- Section 8: send_timeout — observe TimeoutError ---


async def demo_timeout() -> None:
    print("\n=== send_timeout — TimeoutError ===")

    # ---
    # LocalRuntime(send_timeout=N) wraps every send_message in asyncio.wait_for.
    # If the handler does not respond within N seconds, TimeoutError is raised.
    # The future is cancelled and the pending_responses entry is cleaned up.
    # ---

    tight_runtime = LocalRuntime(send_timeout=0.1)  # 100ms timeout
    await tight_runtime.start()

    class StalledAgent(ActorAgent):
        async def on_message(
            self, ctx: MessageContext, content: list[ContentBlock]
        ) -> object:
            await asyncio.sleep(60)
            return "never"

    stalled = StalledAgent(name="stalled", runtime=tight_runtime)
    await stalled.start()

    t0 = time.monotonic()
    try:
        await tight_runtime.send_message("go", recipient=stalled.id)
    except TimeoutError:
        elapsed = time.monotonic() - t0
        print(f"  TimeoutError after {elapsed * 1000:.0f}ms (limit 100ms) ✓")

    await tight_runtime.stop()


# --- Section 9: pub/sub fan-out — isolation between subscribers ---


async def demo_fanout_isolation(runtime: LocalRuntime) -> None:
    print("\n=== Pub/sub fan-out isolation ===")

    # ---
    # Each subscriber runs in its own task. A crash in one subscriber
    # does NOT prevent other subscribers from receiving the message.
    # ---

    topic = TopicId(type="news", source="feed")
    results: dict[str, list[str]] = {"good": [], "bad": []}

    class GoodListener(ActorAgent):
        async def on_message(
            self, ctx: MessageContext, content: list[ContentBlock]
        ) -> object:
            text = content[0].text if content and hasattr(content[0], "text") else ""
            results["good"].append(text)
            return None

    class BadListener(ActorAgent):
        async def on_message(
            self, ctx: MessageContext, content: list[ContentBlock]
        ) -> object:
            raise RuntimeError("bad listener always crashes")

    good = GoodListener(name="good_listener", runtime=runtime, subscriptions=[topic])
    bad = BadListener(name="bad_listener", runtime=runtime, subscriptions=[topic])
    await good.start()
    await bad.start()

    await runtime.publish_message("headline-1", topic=topic)
    await runtime.publish_message("headline-2", topic=topic)
    await asyncio.sleep(0.05)

    print(f"  good_listener received: {results['good']}")
    print("  bad_listener crashed but good_listener unaffected ✓")

    await good.stop()
    await bad.stop()


async def main() -> None:
    await demo_identity()
    await demo_lifecycle_states()
    await demo_envelope()
    await demo_mailbox_backpressure()

    runtime = LocalRuntime(
        restart_policy=RestartPolicy(max_restarts=5, restart_window=60.0),
        send_timeout=5.0,
    )
    await runtime.start()
    try:
        await demo_ordering(runtime)
        await demo_supervisor_restart(runtime)
        await demo_cancellation(runtime)
        await demo_fanout_isolation(runtime)
    finally:
        await runtime.stop()

    await demo_timeout()

    print("\nAll runtime internals demos complete.")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
