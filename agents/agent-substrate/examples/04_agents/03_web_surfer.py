"""04-3 — Web Research Agent with Streaming Output

Demonstrates a ReActAgent that uses WebSearchTool for multi-step web research
and streams partial output tokens to the console in real time.

WebSearchTool (ravi.extensions.tools.builtin_tools) is the built-in search
integration. For full browser automation (click, screenshot, JS execution),
swap it for WebSurferTool from ravi.catalog.tools.web_surfer.tool — which
requires Playwright: uv run playwright install chromium.

Prerequisites: OPENAI_API_KEY set.
"""

import asyncio

from ravi.extensions.agents.react.agent import ReActAgent
from ravi.extensions.tools.builtin_tools import WebSearchTool
from ravi.integrations.llm.openai.openai_client import OpenAIClient
from ravi.kernel.agent_catalog import AgentCatalog
from ravi.kernel.memory.unbounded_memory import UnboundedMemory
from ravi.kernel.messages._types import TextDeltaChunk

# Infrastructure:
# - OPENAI_API_KEY environment variable required
# - No external services needed (WebSearchTool is self-contained)
#
# For real search results, configure WebSearchTool with an API key or swap
# for a tool backed by SerpAPI / Tavily.


async def main() -> None:

    # ---
    # Section 1: Create agent with WebSearchTool

    from ravi.configs.settings import settings
    catalog = AgentCatalog()
    model_name = settings.CHAT_MODEL.split("/")[-1]
    catalog.register_model("primary", OpenAIClient(model=model_name, api_key=settings.OPENAI_API_KEY))
    catalog.register_memory("memory", UnboundedMemory())
    catalog.register_tool(WebSearchTool())

    agent = ReActAgent(
        name="researcher",
        description="Research assistant that searches the web for up-to-date information",
        catalog=catalog,
        system_instructions=(
            "You are a research assistant. Use the web_search tool to find "
            "current information. Synthesise findings into a concise, structured "
            "report with key takeaways."
        ),
        max_iterations=8,
    )

    # ---
    # Section 2: Run a research task

    query = "Research the latest developments in quantum computing in 2025"
    print(f"Query: {query}\n")

    # ---
    # Section 3: Streaming output — print tokens as they arrive

    print("=== Streaming response ===")
    async for chunk in agent.run_stream(query):
        if isinstance(chunk, TextDeltaChunk):
            print(chunk.text, end="", flush=True)

    print("\n\n=== Stream complete ===")

    # ---
    # To use WebSurferTool instead (requires playwright):
    #
    #   from ravi.catalog.tools.web_surfer.tool import WebSurferTool
    #   catalog.register_tool(WebSurferTool(headless=True))
    #
    # WebSurferTool drives a real Chromium browser — it can navigate, click,
    # fill forms, take screenshots, and extract page markdown. It is ideal for
    # sites that require JavaScript or login flows.


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())

