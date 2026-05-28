"""ravi - Async AI-agent framework built on FastAPI.

Layer structure (dependencies flow downward only):
    core          - agents, memory, messages, context, guardrails (pure logic)
    integrations  - LLM, audio, MCP, skills, third-party API adapters
    tools         - built-in tool implementations
    shared        - cross-service infrastructure (auth, events, database, observability)
    server        - monolith FastAPI app, routes, DB models
    services      - microservice FastAPI apps

Recommended imports:
    from ravi.extensions.agents.react.agent import ReActAgent
    from ravi.integrations.llm.openai.openai_client import OpenAIClient
    from ravi.kernel.tools.base_tool import BaseTool, ToolResult

Structured outputs quick-start:
    from ravi import (
        parse, LLMJudge, StructuredRouter,
        ContentSafetyJudge, RelevanceJudge, ClassificationResult,
    )

See ARCHITECTURE.md for extension guides and the full layer diagram.
"""

from __future__ import annotations

# Re-export structured outputs so callers can do:
#   from ravi import parse, LLMJudge, StructuredRouter, ...
from ravi.kernel.structured import (
    ClassificationResult,
    ContentSafetyJudge,
    ExtractionResult,
    RelevanceJudge,
    StructuredOutputError,
    StructuredOutputResult,
)
from ravi.extensions.structured import (
    LLMJudge,
    StructuredRouter,
    parse,
)

# Default agent — the simplest way to use ravi:
#   from ravi import Agent
#   agent = Agent(name="Bot", model="claude-sonnet-4-20250514")
#   result = await agent.run("Hello!")
from ravi.extensions.agents.default.agent import Agent  # noqa: F401

# Batch processing:
#   from ravi import BatchProcessor, BatchConfig
#   processor = BatchProcessor(fn=my_fn, config=BatchConfig(max_concurrency=5))
#   result = await processor.run(inputs)
from ravi.kernel.batch import BatchConfig  # noqa: F401
from ravi.extensions.batch import BatchProcessor  # noqa: F401

# Structured data extraction:
#   from ravi import Extractor, Invoice
#   extractor = Extractor(schema=Invoice, client=client)
#   result = await extractor.extract("Invoice text ...")
from ravi.extensions.extraction import Extractor  # noqa: F401
from ravi.extensions.extraction.schemas import (  # noqa: F401
    BusinessCard,
    Contract,
    Invoice,
    Receipt,
    Resume,
)

# Model client factory + model metadata:
#   from ravi import create_model_client, ModelProfile, ProviderConfig
#   client = create_model_client("gemini/gemini-2.5-flash")
from ravi.integrations.llm.factory import create_model_client  # noqa: F401
from ravi.kernel.llm.models import (  # noqa: F401
    ModelProfile,
    get_model_profile,
    get_context_length,
    estimate_cost,
    list_models,
)
from ravi.kernel.llm.provider import ProviderConfig  # noqa: F401

# Root-level primitives (canonical locations)
from ravi.exceptions import (  # noqa: F401
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
    """Entry point - run uvicorn ravi.server.app:app to start."""
    print("agent-framework - run `uvicorn ravi.server.app:app --port 8001 --reload`")
