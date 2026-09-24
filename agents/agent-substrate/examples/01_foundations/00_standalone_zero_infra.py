"""Example 1-0: Standalone, zero-infra chatbot — proof of the L1 charter.

Every import below comes from ``substrate.agents`` (or stdlib/``dotenv``) —
nothing from ``substrate.integrations`` or ``substrate.serving``. No
Docker, no Postgres, no Redis, no S3: history is
one local JSON file, the runtime is one local SQLite file. This is the
point being demonstrated — every kernel Protocol ``agents/`` implements
(storage, runtime, LLM) ships with exactly one default that needs the least
infrastructure that Protocol can possibly need, so ``agents/`` alone is
enough to run a real, complete chatbot end to end.

The one thing this script still needs from *you*: a model to talk to.
That's not a gap — talking to a model is inherently external, the same as
calling any other tool — so bring an API key (``OPENAI_API_KEY``) or point
``base_url`` at a local Ollama/vLLM/LM Studio server (no key needed there).

Run:
    cd agent-substrate
    uv run examples/01_foundations/00_standalone_zero_infra.py
"""

from __future__ import annotations

import asyncio
import os

from dotenv import load_dotenv

load_dotenv()  # walks up to find the repo-root .env

from substrate.agents import ReActAgent
from substrate.agents.context import ContextConfig
from substrate.agents.llm import OpenAIChatCompletionClient
from substrate.agents.runtime import build_local_runtime
from substrate.kernel.core.content import ChatMessage, Role, TextBlock
from substrate.kernel.core.identity import Actor
from substrate.kernel.messaging.message import ChatPayload, Message


async def main() -> None:
    api_key = os.environ.get("OPENAI_API_KEY")
    base_url = os.environ.get("STANDALONE_DEMO_BASE_URL")  # e.g. a local Ollama server
    if not api_key and not base_url:
        raise SystemExit(
            "Set OPENAI_API_KEY in .env (a real key), or STANDALONE_DEMO_BASE_URL "
            "to a local OpenAI-compatible server (e.g. http://localhost:11434/v1 "
            "for Ollama, api_key can be anything non-empty in that case)."
        )

    model = OpenAIChatCompletionClient(
        model=os.environ.get("STANDALONE_DEMO_MODEL", "gpt-4o-mini"),
        api_key=api_key or "local",
        base_url=base_url,
    )

    agent = ReActAgent(
        "StandaloneBot",
        model=model,
        # No tools, no integrations/ import — a toolless agent is still a
        # complete, runnable chatbot; see the L1 charter for why.
        context=ContextConfig.default(),  # LocalFilesystemHistoryProvider — one JSON file
        system_instructions="You are a helpful assistant.",
        max_iterations=4,
    )

    # build_local_runtime() — SQLite-durable scheduler/event-log/inbox/etc.
    # under one file, zero Docker/Postgres/Redis. A bare Runtime() (pure
    # in-memory, gone on exit) also works if durability isn't needed here.
    async with build_local_runtime(path="./data/db/standalone_demo.sqlite3") as rt:
        await rt.register(agent)

        msg = Message(
            target=agent.id,
            sender=Actor(type="cli", key="standalone_demo"),
            payload=ChatPayload(
                message=ChatMessage(
                    role=Role.USER,
                    content=[TextBlock(text="In one sentence, what is a ReAct agent?")],
                )
            ),
        )
        run_id = await rt.submit(agent.id, msg)

        async for entry in rt.event_log.tail(run_id):
            if entry.kind == "text.delta":
                print(entry.payload.get("text", ""), end="", flush=True)
            elif entry.kind in ("run.completed", "run.failed", "run.cancelled"):
                print()
                if entry.kind != "run.completed":
                    print(f"[{entry.kind}] {entry.payload}")
                break


if __name__ == "__main__":
    asyncio.run(main())
