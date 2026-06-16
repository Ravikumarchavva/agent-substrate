"""Minimal agent bootstrap — the "hello world" of ravi-engine.

This is the absolute minimum needed to get a ReActAgent running.
Start here if you are new to ravi-engine.

Run:
    cd ravi-engine
    uv run python examples/experiments/base_start.py
"""

import asyncio

from ravi.agents.core import ReActAgent
from ravi.integrations.llm.openai.openai_client import OpenAIClient
from ravi.kernel.agent_catalog import AgentCatalog
from ravi.agents.context import InMemoryHistoryProvider

# Infrastructure: OPENAI_API_KEY environment variable (read automatically by OpenAIClient)

# ---


async def main() -> None:
    # --- 1. Build the catalog (registry of resources the agent can use) ---
    catalog = AgentCatalog()
    catalog.register_model("primary", OpenAIClient(model="gpt-4o-mini"))
    catalog.register_memory("memory", InMemoryHistoryProvider())

    # --- 2. Create the agent ---
    agent = ReActAgent(
        name="hello-agent",
        description="A simple helpful assistant",
        catalog=catalog,
        verbose=False,
    )

    # --- 3. Run with a simple question ---
    result = await agent.run("What is the capital of France, and what is 12 * 8?")

    # --- 4. Print the result ---
    # result.output_text extracts plain text from the multimodal output list
    print(result.output_text)


if __name__ == "__main__":
    asyncio.run(main())
