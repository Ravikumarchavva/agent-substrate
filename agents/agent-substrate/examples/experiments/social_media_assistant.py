"""Social media content assistant with custom tools.

Demonstrates how to define custom BaseTool subclasses and combine them with
built-in tools (WebSearchTool) inside a AssistantAgent.  The agent researches a
topic, extracts hashtags, and formats a platform-specific post.

Run:
    cd ravi-engine
    uv run python examples/experiments/social_media_assistant.py
"""

import asyncio
import json
import re
from typing import ClassVar

from ravi.reasoning.agents.assistant import AssistantAgent
from ravi.fabric.tools.builtin_tools import WebSearchTool
from ravi.integrations.llm.openai.openai_client import OpenAIClient
from ravi.kernel.agent_catalog import AgentCatalog
from ravi.fabric.memory.unbounded import UnboundedMemory
from ravi.kernel.messages.content import TextBlock
from ravi.kernel.messages._types import TextDeltaChunk
from ravi.kernel.tools.base_tool import BaseTool, ToolResult, ToolRisk

# Infrastructure: OPENAI_API_KEY environment variable

# ---


class AnalyzeHashtagsTool(BaseTool):
    """Parses and ranks hashtags found in text."""

    risk: ClassVar[ToolRisk] = ToolRisk.SAFE

    def __init__(self) -> None:
        super().__init__(
            name="analyze_hashtags",
            description=(
                "Extracts hashtags from text and returns them ranked by likely "
                "engagement value.  Pass raw post content or a list of topics."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Text to scan for hashtags or topic keywords",
                    }
                },
                "required": ["text"],
            },
        )

    async def execute(self, *, text: str) -> ToolResult:  # type: ignore[override]
        # Extract explicit #tags
        found = re.findall(r"#(\w+)", text)
        # Also generate tags from long words if none were found
        if not found:
            found = [w.strip(".,!?").lower() for w in text.split() if len(w) > 5]
        # Deduplicate, limit to top 8
        seen: set[str] = set()
        unique = []
        for tag in found:
            if tag.lower() not in seen:
                seen.add(tag.lower())
                unique.append(f"#{tag}")
            if len(unique) >= 8:
                break
        result = {"hashtags": unique, "count": len(unique)}
        return ToolResult(
            content=[TextBlock(text=json.dumps(result))],
            is_error=False,
        )


class FormatPostTool(BaseTool):
    """Formats content into a platform-specific social media post."""

    risk: ClassVar[ToolRisk] = ToolRisk.SAFE

    def __init__(self) -> None:
        super().__init__(
            name="format_post",
            description=(
                "Reformats raw content into a polished post for a specific platform. "
                "Supported platforms: twitter, linkedin, instagram."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "The raw post content or draft text",
                    },
                    "platform": {
                        "type": "string",
                        "enum": ["twitter", "linkedin", "instagram"],
                        "description": "Target social media platform",
                    },
                },
                "required": ["content", "platform"],
            },
        )

    async def execute(self, *, content: str, platform: str) -> ToolResult:  # type: ignore[override]
        platform = platform.lower()
        if platform == "twitter":
            post = content[:280]
            if len(content) > 280:
                post = content[:277] + "..."
            formatted = f"[Twitter / X]\n{post}"
        elif platform == "linkedin":
            formatted = (
                f"[LinkedIn]\n\n{content}\n\n"
                "I'd love to hear your thoughts — drop a comment below! "
                "#AI #Technology #Innovation"
            )
        elif platform == "instagram":
            formatted = f"[Instagram]\n\n{content}\n\n."
        else:
            formatted = content

        return ToolResult(
            content=[TextBlock(text=formatted)],
            is_error=False,
        )


# ---


async def build_agent() -> AssistantAgent:
    """Construct and return the social media assistant agent."""
    catalog = AgentCatalog()
    catalog.register_model("primary", OpenAIClient(model="gpt-4o-mini"))
    catalog.register_memory("memory", UnboundedMemory())
    catalog.register_tool(WebSearchTool())
    catalog.register_tool(AnalyzeHashtagsTool())
    catalog.register_tool(FormatPostTool())

    return AssistantAgent(
        name="social-media-assistant",
        description=(
            "A social media content assistant that researches topics, "
            "extracts hashtags, and creates platform-specific posts."
        ),
        system_instructions=(
            "You are a social media content expert. When asked to create a post:\n"
            "1. Use web_search to research the topic.\n"
            "2. Use analyze_hashtags to identify relevant hashtags.\n"
            "3. Use format_post to produce the final platform-specific post.\n"
            "Return the formatted post as your final answer."
        ),
        catalog=catalog,
        verbose=True,
    )


async def main() -> None:
    agent = await build_agent()

    query = (
        "Research trending AI topics and create an engaging LinkedIn post "
        "with relevant hashtags."
    )
    print(f"Query: {query}\n")
    print("--- Streaming output ---")

    # --- Streaming run ---
    async for chunk in await agent.run_stream(query):
        if isinstance(chunk, TextDeltaChunk):
            print(chunk.text, end="", flush=True)

    print("\n--- Done ---")


if __name__ == "__main__":
    asyncio.run(main())
