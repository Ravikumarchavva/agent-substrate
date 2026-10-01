"""Example 1-0: Standalone, zero-infra chatbot — proof of the L1 charter.

Every import below comes from ``substrate.kernel`` (or stdlib/``dotenv``) —
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

from substrate.kernel import ReActAgent
from substrate.kernel.context import ContextConfig
from substrate.kernel.llm import OpenAICompatibleClient
from substrate.kernel.runtime import Runtime
from substrate.kernel.abstractions.core.content import ChatMessage, Role, TextBlock
from substrate.kernel.abstractions.core.identity import Actor
from substrate.kernel.abstractions.messaging.message import ChatPayload, Message


async def main() -> None:
    api_key = os.environ.get("OPENAI_API_KEY")
    base_url = os.environ.get("STANDALONE_DEMO_BASE_URL")  # e.g. a local Ollama server
    if not api_key and not base_url:
        raise SystemExit(
            "Set OPENAI_API_KEY in .env (a real key), or STANDALONE_DEMO_BASE_URL "
            "to a local OpenAI-compatible server (e.g. http://localhost:11434/v1 "
            "for Ollama, api_key can be anything non-empty in that case)."
        )

    model = OpenAICompatibleClient(
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

    # Runtime.local() — one SQLite file holds the whole durable runtime:
    # runs, journal, inbox, signals. Zero Docker/Postgres/Redis. Pass ":memory:" for a
    # throwaway one that is gone on exit.
    async with Runtime.local("./data/db/standalone_demo.sqlite3") as rt:
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

        async for entry in rt.tail(run_id):
            if entry.kind == "text.delta":
                print(entry.payload.get("text", ""), end="", flush=True)
            elif entry.kind in ("run.completed", "run.failed", "run.cancelled"):
                print()
                if entry.kind != "run.completed":
                    print(f"[{entry.kind}] {entry.payload}")
                break


if __name__ == "__main__":
    asyncio.run(main())
