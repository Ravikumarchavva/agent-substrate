"""Conversations that survive restarts.

State lives in one folder. ``Store.at(folder)`` is what agents, the runtime and your own code all share: threads (the
conversation history), the run journal, and later memory and files. Nothing to install or start — and nothing lost
when the process dies, because every committed turn is already on disk.

A *thread* is one conversation. Runs on the same thread see what was said before; here the "restart" is a brand
new ``Runtime`` on the same folder.

    uv run python examples/03_conversations.py
"""

from __future__ import annotations

import asyncio

from _model import pick_model

from substrate.agents import ReActAgent
from substrate.context import ContextConfig
from substrate.runtime import Runtime
from substrate.stores import Store
from substrate.types import Role


def build_agent(store: Store) -> ReActAgent:
    return ReActAgent(
        "assistant",
        # Offline, the "model" answers with every user message it was shown — so you can see what the engine remembered.
        model=pick_model(
            lambda messages: "I remember: " + " | ".join(m.text for m in messages if m.role == Role.USER),
        ),
        context=ContextConfig(store.threads),  # the agent reads and writes the thread in the same store
    )


async def main() -> None:
    folder = "./.substrate"

    # First process: say something.
    store = Store.at(folder)
    async with Runtime(store) as runtime:
        first = await runtime.run(build_agent(store), "My name is Ada.", thread="ada")
        print("1st run:", first.output)
    await store.aclose()

    # ...the process is gone. A new one opens the same folder and carries on.
    store = Store.at(folder)
    async with Runtime(store) as runtime:
        second = await runtime.run(build_agent(store), "What did I tell you?", thread="ada")
        print("after restart:", second.output)

        # The conversation is a DAG of messages you can read back, branch and fork.
        branch = await store.threads.get_branch("ada", "main")
        print("messages on the thread:", sum(1 for _ in await _walk(store, branch.head_message_id)))
    await store.aclose()


async def _walk(store: Store, node_id: str | None) -> list[str]:
    nodes: list[str] = []
    while node_id is not None:
        node = await store.threads.get_node(node_id)
        nodes.append(node.payload.text)
        node_id = node.parent_id
    return nodes


if __name__ == "__main__":
    asyncio.run(main())
