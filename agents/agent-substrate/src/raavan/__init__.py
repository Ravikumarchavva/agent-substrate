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

# Default agent — the simplest way to use raavan:
#   from raavan import Agent
#   agent = Agent(name="Bot", model="claude-sonnet-4-20250514")
#   result = await agent.run("Hello!")
from raavan.core.agents.default_agent import Agent  # noqa: F401

# Batch processing:
#   from raavan import BatchProcessor, BatchConfig
#   processor = BatchProcessor(fn=my_fn, config=BatchConfig(max_concurrency=5))
#   result = await processor.run(inputs)
from raavan.core.batch import BatchConfig, BatchProcessor  # noqa: F401

# Structured data extraction:
#   from raavan import Extractor, Invoice
#   extractor = Extractor(schema=Invoice, client=client)
#   result = await extractor.extract("Invoice text ...")
from raavan.core.extraction import Extractor  # noqa: F401
from raavan.core.extraction.schemas import (  # noqa: F401
    BusinessCard,
    Contract,
    Invoice,
    Receipt,
    Resume,
)

# Model client factory + model metadata:
#   from raavan import create_model_client, ModelProfile, ProviderConfig
#   client = create_model_client("gemini/gemini-2.5-flash")
from raavan.integrations.llm.factory import create_model_client  # noqa: F401
from raavan.core.llm.models import (  # noqa: F401
    ModelProfile,
    get_model_profile,
    get_context_length,
    estimate_cost,
    list_models,
)
from raavan.core.llm.provider import ProviderConfig  # noqa: F401

# Root-level primitives (canonical locations)
from raavan.exceptions import (  # noqa: F401
    AgentError,
    AgentExecutionError,
    ConfigurationError,
    ContextLimitExceededError,
    GuardrailError,
    GuardrailTripwireError,
    ModelProviderError,
    ToolError,
    ToolExecutionError,
    ToolNotFoundError,
)

__all__ = [
    "Agent",
    "BatchConfig",
    "BatchProcessor",
    "BusinessCard",
    "Contract",
    "Extractor",
    "Invoice",
    "ModelProfile",
    "ProviderConfig",
    "Receipt",
    "Resume",
    "create_model_client",
    "estimate_cost",
    "get_context_length",
    "get_model_profile",
    "list_models",
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
