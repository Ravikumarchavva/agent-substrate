"""Interactive demo of the social media assistant.

Runs the social media assistant agent against three demo topics and generates
formatted posts for both Twitter and LinkedIn.  Swap `DEMO_TOPICS` for your
own list, or replace it with `input("Enter topic: ")` for live interaction.

Run:
    cd ravi-engine
    uv run python examples/experiments/social_media_demo.py
"""

import asyncio
import sys
from pathlib import Path

# Allow sibling-module imports (social_media_assistant lives in the same folder)
sys.path.insert(0, str(Path(__file__).parent))

from ravi.extensions.agents.react.agent import ReActAgent
from ravi.extensions.tools.builtin_tools import WebSearchTool
from ravi.integrations.llm.openai.openai_client import OpenAIClient
from ravi.kernel.agent_catalog import AgentCatalog
from ravi.kernel.memory.unbounded_memory import UnboundedMemory

# Infrastructure: OPENAI_API_KEY environment variable

DEMO_TOPICS = [
    "large language models in healthcare",
    "sustainable AI and green computing",
    "agentic AI workflows replacing traditional automation",
]

PLATFORMS = ["twitter", "linkedin"]

# ---


def build_agent() -> ReActAgent:
    """Build a fresh agent (fresh memory per demo session)."""
    # Import here so social_media_assistant.py tool classes can be reused
    from social_media_assistant import (  # noqa: PLC0415
        AnalyzeHashtagsTool,
        FormatPostTool,
    )

    catalog = AgentCatalog()
    catalog.register_model("primary", OpenAIClient(model="gpt-4o-mini"))
    catalog.register_memory("memory", UnboundedMemory())
    catalog.register_tool(WebSearchTool())
    catalog.register_tool(AnalyzeHashtagsTool())
    catalog.register_tool(FormatPostTool())

    return ReActAgent(
        name="social-media-demo",
        description="Social media content demo agent",
        system_instructions=(
            "You are a social media content expert. For each request:\n"
            "1. Research the topic with web_search.\n"
            "2. Extract hashtags with analyze_hashtags.\n"
            "3. Call format_post once per platform requested.\n"
            "Return each formatted post clearly labelled."
        ),
        catalog=catalog,
        verbose=False,
    )


async def run_topic(agent: ReActAgent, topic: str) -> None:
    """Run the agent for a single topic across all demo platforms."""
    platforms_str = " and ".join(PLATFORMS)
    query = (
        f"Research '{topic}' and create posts formatted for {platforms_str}. "
        "Include relevant hashtags in each post."
    )

    # --- Run ---
    result = await agent.run(query)

    print(f"\n{'=' * 60}")
    print(f"TOPIC: {topic}")
    print("=" * 60)
    print(result.output_text)


async def main() -> None:
    # --- 1. Setup ---
    agent = build_agent()

    # --- 2. Run demo topics ---
    # For live interactive mode, replace DEMO_TOPICS with:
    #   topics = [input("Enter topic: ")]
    for topic in DEMO_TOPICS:
        await run_topic(agent, topic)
        # Reset memory between topics so conversations don't bleed into each other
        await agent.reset()

    # --- 3. Summary ---
    print(f"\n{'=' * 60}")
    print(f"Demo complete. Processed {len(DEMO_TOPICS)} topics.")


if __name__ == "__main__":
    asyncio.run(main())
