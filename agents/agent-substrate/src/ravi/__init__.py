"""ravi - Async AI-agent framework built on FastAPI.

Layer structure (dependencies flow downward only):
    core          - agents, memory, messages, context, guardrails (pure logic)
    integrations  - LLM, audio, MCP, skills, third-party API adapters
    tools         - built-in tool implementations
    shared        - cross-service infrastructure (auth, events, database, observability)
    server        - monolith FastAPI app, routes, DB models
    services      - microservice FastAPI apps

Recommended imports:
    from ravi.reasoning.agents.assistant.agent import AssistantAgent
    from ravi.orchestration.agents.proxy.agent import UserProxyAgent
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
from ravi.reasoning.structured import (
    LLMJudge,
    StructuredRouter,
    parse,
)

# Actor-model agents — the standard way to use ravi:
#   from ravi import AssistantAgent, UserProxyAgent
#   async with LocalRuntime() as rt:
#       agent = AssistantAgent("bot", rt, catalog=catalog)
#       await agent.start()
#       proxy = UserProxyAgent("proxy", rt)
#       await proxy.start()
#       result = await proxy.ask("Hello!", recipient=agent.id)
from ravi.reasoning.agents.assistant.agent import AssistantAgent  # noqa: F401
from ravi.orchestration.agents.proxy.agent import UserProxyAgent  # noqa: F401

# Batch processing:
#   from ravi import BatchProcessor, BatchConfig
#   processor = BatchProcessor(fn=my_fn, config=BatchConfig(max_concurrency=5))
#   result = await processor.run(inputs)
from ravi.fabric.batch import BatchConfig  # noqa: F401
from ravi.fabric.batch import BatchProcessor  # noqa: F401

# Structured data extraction:
#   from ravi import Extractor, Invoice
#   extractor = Extractor(schema=Invoice, client=client)
#   result = await extractor.extract("Invoice text ...")
from ravi.reasoning.extraction import Extractor  # noqa: F401
from ravi.reasoning.extraction.schemas import (  # noqa: F401
    BusinessCard,
    Contract,
    Invoice,
    Receipt,
    Resume,
)

# Model client factory + model metadata:
#   from ravi import LLMFactory, ModelProfile
#   client = LLMFactory("gemini-2.5-flash", api_key).build()
from ravi.integrations.llm.factory import LLMFactory, create_model_client  # noqa: F401
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
    "LLMFactory",
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
