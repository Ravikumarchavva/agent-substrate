"""raavan - Async AI-agent framework built on FastAPI.

Layer structure (dependencies flow downward only):
    core          - agents, memory, messages, context, guardrails (pure logic)
    integrations  - LLM, audio, MCP, skills, third-party API adapters
    tools         - built-in tool implementations
    shared        - cross-service infrastructure (auth, events, database, observability)
    server        - monolith FastAPI app, routes, DB models
    services      - microservice FastAPI apps

Recommended imports:
    from raavan.core.agents.react_agent import ReActAgent
    from raavan.integrations.llm.openai.openai_client import OpenAIClient
    from raavan.core.tools.base_tool import BaseTool, ToolResult

Structured outputs quick-start:
    from raavan import (
        parse, LLMJudge, StructuredRouter,
        ContentSafetyJudge, RelevanceJudge, ClassificationResult,
    )

See ARCHITECTURE.md for extension guides and the full layer diagram.
"""

from __future__ import annotations

# Re-export structured outputs so callers can do:
#   from raavan import parse, LLMJudge, StructuredRouter, ...
from raavan.core.structured import (
    ClassificationResult,
    ContentSafetyJudge,
    ExtractionResult,
    LLMJudge,
    RelevanceJudge,
    StructuredOutputError,
    StructuredOutputResult,
    StructuredRouter,
    parse,
)

__all__ = [
    "parse",
    "LLMJudge",
    "StructuredRouter",
    "StructuredOutputResult",
    "StructuredOutputError",
    "ContentSafetyJudge",
    "RelevanceJudge",
    "ClassificationResult",
    "ExtractionResult",
]


def main() -> None:
    """Entry point - run uvicorn raavan.server.app:app to start."""
    print("agent-framework - run `uvicorn raavan.server.app:app --port 8001 --reload`")
