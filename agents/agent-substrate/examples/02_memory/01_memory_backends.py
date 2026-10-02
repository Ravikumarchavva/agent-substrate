"""Example 2-1: Memory Backends — raw history operations across the storage tiers.

DurableThreadStore (Postgres) is the real default — conversation history
that survives a restart. LocalFilesystemThreadStore is the no-infra
durable floor: still survives a restart, no Docker/Postgres required, just a
folder on disk. There is no in-memory provider: one that forgets on exit
is not something an agent's conversation can rest on.

Both implement the same conversation-DAG ThreadStore contract
(``append_node`` / ``append_and_advance`` / ``get_branch`` / ...) — a linear
transcript is a *projection* of one branch, read via
``substrate.context.history.project_messages``, not stored separately.

Demonstrates using:
  - LocalFilesystemThreadStore (durable, no infra — the default floor)
  - DurableThreadStore (Postgres — the production default, requires DB)
"""

from __future__ import annotations

import asyncio
import os
import tempfile

from substrate.stores import LocalFilesystemThreadStore
from substrate.context import project_messages
from substrate.integrations.history import DurableThreadStore
from substrate.types import ChatMessage, Role, TextBlock
from substrate.stores import MessageNode

DB_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/agentdb",
)


async def _demo(label: str, provider, session_id: str) -> None:
    """Append a user/assistant turn as two DAG nodes, then read the branch
    back as a linear transcript — the same round trip every backend supports
    identically, since all three implement the one ThreadStore contract."""
    print(f"\n=== {label} ===")

    user_node = MessageNode(
        session_id=session_id,
        parent_id=None,
        payload=ChatMessage(role=Role.USER, content=[TextBlock(text="What is the capital of France?")]),
    )
    await provider.append_and_advance(user_node, "main", expected_head_id=None)

    assistant_node = MessageNode(
        session_id=session_id,
        parent_id=user_node.id,
        payload=ChatMessage(role=Role.ASSISTANT, content=[TextBlock(text="Paris.")]),
    )
    await provider.append_and_advance(assistant_node, "main", expected_head_id=user_node.id)

    messages = await project_messages(provider, session_id)
    print(f"  Messages stored: {len(messages)}")
    for m in messages:
        print(f"    [{m.role}]: {[b.text for b in m.content if isinstance(b, TextBlock)]}")

    await provider.delete_session(session_id)
    print("  Session cleared.")


async def main() -> None:
    # 1. LocalFilesystemThreadStore — durable across restarts, zero
    # external infra: "a folder and everything dumps there." The floor
    # every deployment gets even with no Docker/Postgres available.
    with tempfile.TemporaryDirectory() as tmp:
        await _demo(
            "1. LocalFilesystemThreadStore (durable, no infra required)",
            LocalFilesystemThreadStore(root=tmp),
            session_id="demo-session-local",
        )

    # 2. DurableThreadStore — the production default. Postgres-backed,
    # survives a restart, and scales to multiple worker processes.
    try:
        pg_provider = DurableThreadStore(DB_URL)
        await pg_provider.connect()
        await _demo(
            "2. DurableThreadStore (durable default, requires PostgreSQL)",
            pg_provider,
            session_id="demo-session-pg",
        )
        await pg_provider.disconnect()
    except Exception as exc:
        print(f"  [SKIP] Postgres unavailable: {exc}")


if __name__ == "__main__":
    asyncio.run(main())
