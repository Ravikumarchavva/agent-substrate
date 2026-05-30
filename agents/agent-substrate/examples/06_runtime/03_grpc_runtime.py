"""GrpcRuntime — gRPC-backed distributed agent dispatch.

Shows how to use ``GrpcRuntime`` to route agent messages across process
boundaries via gRPC unary RPCs.  The wire format is JSON-encoded envelopes
over a generic gRPC handler — no ``.proto`` compilation required.

Runtime hierarchy:

    BaseRuntime (ABC, ravi.kernel.runtime)
    ├── LocalRuntime            — in-process asyncio (zero infra)
    └── BaseRemoteRuntime       — adds _remote_send abstraction
        ├── GrpcRuntime         — gRPC unary stubs  ← this file
        └── (RestateRuntime)    — Restate durable execution

Infrastructure: grpcio package (``uv add grpcio``).
Graceful fallback if grpcio is not installed.
"""

from __future__ import annotations

import asyncio

from ravi.kernel.runtime import AgentId
from ravi.agents.runtime import LocalRuntime
from ravi.kernel.runtime._contracts import MessageContext
from ravi.kernel.messages.content import ContentBlock
from ravi.agents.actors.actor import ActorAgent

# ---
# What GrpcRuntime adds over LocalRuntime:
#
#   Location transparency — AgentId routing key is the same whether the
#   target agent is in-process or on a remote pod.  The caller's code
#   does not change; only the runtime configuration differs.
#
#   Network routing — remote_peers dict maps agent_type → grpc_address.
#   When send_message targets a type with a remote peer, GrpcRuntime
#   serialises the envelope as JSON and makes a unary gRPC call.
#
#   Horizontal scale — many workers, each hosting different agent types,
#   all reachable through the same AgentRuntime protocol.
# ---


# --- Section 1: verify runtime hierarchy ---


def show_hierarchy() -> None:
    print("=== Runtime inheritance hierarchy ===")

    from ravi.agents.runtime import BaseRuntime, LocalRuntime as LR
    from ravi.adapters.runtime import BaseRemoteRuntime

    print(f"  LocalRuntime -> BaseRuntime:      {issubclass(LR, BaseRuntime)}")
    print(
        f"  BaseRemoteRuntime -> BaseRuntime: {issubclass(BaseRemoteRuntime, BaseRuntime)}"
    )

    try:
        from ravi.adapters.runtime.grpc import GrpcRuntime

        print(
            f"  GrpcRuntime -> BaseRemoteRuntime: {issubclass(GrpcRuntime, BaseRemoteRuntime)}"
        )
        print(
            f"  GrpcRuntime -> BaseRuntime:       {issubclass(GrpcRuntime, BaseRuntime)}"
        )
        return GrpcRuntime
    except ImportError as exc:
        print(f"  GrpcRuntime not importable: {exc}")
        return None


# --- Section 2: local echo agent shared between both runtime modes ---


class EchoAgent(ActorAgent):
    """Returns the received text with an echo prefix."""

    async def on_message(
        self, ctx: MessageContext, content: list[ContentBlock]
    ) -> object:
        text = content[0].text if content and hasattr(content[0], "text") else ""
        return {"echo": text, "agent": self.name, "key": self.key}


class GreeterAgent(ActorAgent):
    """Returns a greeting for the given name."""

    async def on_message(
        self, ctx: MessageContext, content: list[ContentBlock]
    ) -> object:
        text = content[0].text if content and hasattr(content[0], "text") else "world"
        return {"greeting": f"Hello, {text}!", "from": self.name}


# --- Section 3: same agent handler, two runtimes — identical call site ---


async def demo_local_baseline() -> None:
    print("\n=== LocalRuntime baseline (same handler, in-process) ===")

    runtime = LocalRuntime(send_timeout=5.0)
    await runtime.start()

    echo = EchoAgent(name="echo", runtime=runtime)
    greeter = GreeterAgent(name="greeter", runtime=runtime)
    await echo.start()
    await greeter.start()

    r1 = await runtime.send_message("ping from local", recipient=echo.id)
    print(f"  echo:    {r1}")

    r2 = await runtime.send_message("LocalRuntime", recipient=greeter.id)
    print(f"  greeter: {r2}")

    print(f"  registered_types: {runtime.registered_types}")
    print(f"  worker_id:        {runtime.worker_id}")

    await echo.stop()
    await greeter.stop()
    await runtime.stop()


async def demo_grpc_runtime() -> None:
    print("\n=== GrpcRuntime (gRPC-backed dispatch) ===")

    try:
        import grpc  # noqa: F401
    except ImportError:
        print("  grpcio not installed — skipping gRPC demo.")
        print("  Install with: uv add grpcio")
        return

    from ravi.adapters.runtime.grpc import GrpcRuntime

    # ---
    # Single-node setup: GrpcRuntime serving agents on 0.0.0.0:50051.
    # In production you add remote_peers for cross-node dispatch:
    #
    #   GrpcRuntime(
    #       listen_address="0.0.0.0:50051",
    #       remote_peers={
    #           "summarizer": "node-b.cluster.local:50051",
    #           "translator": "node-c.cluster.local:50051",
    #       },
    #   )
    #
    # The call site is identical to LocalRuntime — only the constructor differs.
    # ---

    runtime = GrpcRuntime(listen_address="0.0.0.0:50051")
    # GrpcRuntime requires agents to be registered BEFORE runtime.start()
    echo = EchoAgent(name="echo", runtime=runtime)
    greeter = GreeterAgent(name="greeter", runtime=runtime)
    await echo.start()
    await greeter.start()
    await runtime.start()

    print("  GrpcRuntime started: listen=0.0.0.0:50051")
    print(f"  registered_types: {runtime.registered_types}")
    print(f"  worker_id:        {runtime.worker_id}")

    # Local dispatch (same process)
    r1 = await runtime.send_message("ping via gRPC runtime", recipient=echo.id)
    print(f"  echo (local):    {r1}")

    r2 = await runtime.send_message("GrpcRuntime", recipient=greeter.id)
    print(f"  greeter (local): {r2}")

    await echo.stop()
    await greeter.stop()
    await runtime.stop()
    print("  GrpcRuntime stopped cleanly.")


async def demo_grpc_remote_dispatch() -> None:
    print("\n=== GrpcRuntime cross-node dispatch pattern ===")

    try:
        import grpc  # noqa: F401
    except ImportError:
        print("  grpcio not installed — skipping.")
        return

    from ravi.adapters.runtime.grpc import GrpcRuntime

    # ---
    # Two-node setup:
    #   Node A (this process) — hosts echo + greeter
    #   Node B (remote)       — hosts summarizer (not running in this demo)
    #
    # When node A receives a send_message for "summarizer", it looks up
    # remote_peers["summarizer"] and makes a gRPC call to node B.
    # The routing is transparent — agent code never changes.
    # ---

    runtime_a = GrpcRuntime(
        listen_address="0.0.0.0:50052",
        remote_peers={
            # Pointing at localhost:50053 (node B, not running here) to show config
            "summarizer": "localhost:50053",
        },
    )
    # GrpcRuntime requires agents to be registered BEFORE runtime.start()
    echo = EchoAgent(name="echo", runtime=runtime_a)
    await echo.start()
    await runtime_a.start()

    print("  Node A started: listen=0.0.0.0:50052")
    print(f"  Local agents:  {runtime_a.registered_types}")
    print("  Remote peers:  summarizer → localhost:50053")

    # Local dispatch works fine
    r = await runtime_a.send_message("local call", recipient=echo.id)
    print(f"  Local echo:    {r}")

    # Remote dispatch attempt (node B not running → fails within timeout)
    try:
        await asyncio.wait_for(
            runtime_a.send_message(
                "remote call",
                recipient=AgentId(type="summarizer", key="s1"),
            ),
            timeout=3.0,
        )
    except asyncio.TimeoutError:
        print("  Remote to node B timed out (node B not running — expected)")
        print("  This would succeed when node B is running.")
    except (RuntimeError, Exception) as exc:
        print(f"  Remote to node B (expected failure): {type(exc).__name__}")
        print("  This would succeed when node B is running.")

    await echo.stop()
    await runtime_a.stop()


# --- Section 4: what gRPC adds over LocalRuntime (summary) ---


def show_grpc_vs_local() -> None:
    print("\n=== GrpcRuntime vs LocalRuntime ===")
    comparison = [
        ("Handler code", "unchanged", "unchanged"),
        ("Call site", "send_message()", "send_message()"),
        ("Transport", "asyncio Queue", "gRPC unary JSON"),
        ("Process boundary", "no", "yes"),
        ("remote_peers", "n/a", "agent_type → host:port"),
        ("Serialization", "Python objects", "JSON-encoded Envelope"),
        ("Proto compilation", "n/a", "not needed (generic handler)"),
        ("Location change", "restart process", "update remote_peers"),
    ]
    header = f"  {'Feature':<25} {'LocalRuntime':<24} {'GrpcRuntime'}"
    print(header)
    print("  " + "-" * 70)
    for feature, local, grpc in comparison:
        print(f"  {feature:<25} {local:<24} {grpc}")


async def main() -> None:
    GrpcRuntime = show_hierarchy()
    await demo_local_baseline()
    await demo_grpc_runtime()
    await demo_grpc_remote_dispatch()
    show_grpc_vs_local()
    print("\nAll gRPC runtime demos complete.")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
