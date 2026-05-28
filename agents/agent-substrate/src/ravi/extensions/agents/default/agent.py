"""Default framework-native agent — zero-config entry point for ravi.

Provides an ``Agent`` class similar to Agentor's ``Agentor`` — a high-level
convenience wrapper that auto-detects the LLM provider, wires up memory,
context, and tools, and lets you run or stream immediately.

Examples::

    from ravi import Agent

    # Simplest — uses OPENAI_API_KEY from env, default model
    agent = Agent(name="Assistant")
    result = await agent.run("What is the capital of France?")
    print(result.final_output)

    # With Claude
    agent = Agent(
        name="Claude Bot",
        model="claude-sonnet-4-20250514",
        api_key="sk-ant-...",
    )
    result = await agent.run("Explain quantum computing.")

    # With Gemini + tools
    agent = Agent(
        name="Research Bot",
        model="gemini/gemini-2.5-flash",
        api_key="AIza...",
        tools=[MyCustomTool()],
    )
    result = await agent.run("Search for latest AI papers.")

    # Streaming
    async for chunk in agent.run_stream("Write a story"):
        print(chunk, end="", flush=True)
"""

from __future__ import annotations

import os
from typing import Any, AsyncIterator, Dict, List, Optional, Type

from ravi.kernel.agent_catalog._catalog import AgentCatalog
from ravi.extensions.agents.react.agent import ReActAgent
from ravi.kernel.agents.agent_result import AgentRunResult
from ravi.kernel.batch.config import BatchConfig, BatchResult
from ravi.extensions.batch.processor import BatchProcessor
from ravi.extensions.context import SlidingWindowContext
from ravi.kernel.llm.base_client import BaseModelClient
from ravi.kernel.memory.base_memory import BaseMemory
from ravi.kernel.memory.unbounded_memory import UnboundedMemory
from ravi.kernel.tools.base_tool import BaseTool
from ravi.integrations.llm.factory import create_model_client, detect_provider


def _first_env_value(*names: str) -> str:
    """Return the first non-empty environment value from the provided names."""
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


class Agent:
    """Framework-native agent — the simplest way to use ravi.

    Auto-detects the LLM provider from the model string, creates the
    appropriate client, wires up memory and context, and provides ``run()``
    and ``run_stream()`` for immediate use.

    Parameters:
        name: Human-readable agent name.
        model: Model identifier (e.g. ``"gpt-5-mini"``, ``"claude-sonnet-4-20250514"``,
               ``"gemini/gemini-2.5-flash"``). Defaults to ``"gpt-5-mini"``.
        instructions: System instructions for the agent.
        api_key: API key for the detected provider. Defaults to env vars
                 (``OPENAI_API_KEY``, ``ANTHROPIC_API_KEY``, ``GOOGLE_API_KEY``).
        tools: List of ``BaseTool`` instances to give the agent.
        memory: Custom memory backend. Defaults to ``UnboundedMemory``.
        temperature: Temperature for LLM generation.
        max_tokens: Max output tokens per generation.
        max_iterations: Max ReAct loop iterations.
        context_window: Number of messages to include in each LLM call.
        response_schema: Optional Pydantic model for structured output.
        model_client: Pre-built model client (overrides model/api_key/temperature).
    """

    def __init__(
        self,
        name: str = "Agent",
        *,
        model: str = "gpt-5-mini",
        instructions: Optional[str] = None,
        api_key: Optional[str] = None,
        tools: Optional[List[BaseTool]] = None,
        memory: Optional[BaseMemory] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        max_iterations: int = 30,
        context_window: int = 40,
        response_schema: Optional[Type[Any]] = None,
        model_client: Optional[BaseModelClient] = None,
    ):
        self.name = name
        self.model = model
        self.instructions = instructions or (
            "You are a helpful AI assistant. Think step-by-step and use "
            "the provided tools when needed to answer the user's request."
        )
        self.tools = tools or []
        self.max_iterations = max_iterations
        self.context_window = context_window
        self.response_schema = response_schema

        # Build or accept model client
        if model_client is not None:
            self._model_client = model_client
        else:
            api_keys = self._resolve_api_keys(model, api_key)
            self._model_client = create_model_client(
                model,
                api_keys=api_keys,
                temperature=temperature,
                max_tokens=max_tokens,
            )

        # Memory
        self._memory = memory or UnboundedMemory()

    @staticmethod
    def _resolve_api_keys(model: str, api_key: Optional[str] = None) -> Dict[str, str]:
        """Resolve API keys from explicit param or environment variables."""
        keys: Dict[str, str] = {}

        # Read all possible keys from environment, including common aliases.
        keys["openai"] = _first_env_value("OPENAI_API_KEY")
        keys["anthropic"] = _first_env_value("ANTHROPIC_API_KEY")
        keys["groq"] = _first_env_value("GROQ_API_KEY", "GROK_API_KEY")
        keys["google"] = _first_env_value("GOOGLE_API_KEY", "GEMINI_API_KEY")
        keys["openrouter"] = _first_env_value("OPENROUTER_API_KEY")

        # If explicit api_key provided, override the detected provider
        if api_key:
            provider = detect_provider(model)
            if provider == "openai":
                keys["openai"] = api_key
            elif provider == "groq":
                keys["groq"] = api_key
            elif provider == "anthropic":
                keys["anthropic"] = api_key
            elif provider == "gemini":
                keys["google"] = api_key
            elif provider == "openrouter":
                keys["openrouter"] = api_key

        return keys

    def _build_agent(self) -> ReActAgent:
        """Build a fresh ``ReActAgent`` for a single run."""
        catalog = AgentCatalog()
        catalog.register_model("primary", self._model_client)
        catalog.register_context(
            "default", SlidingWindowContext(max_messages=self.context_window)
        )
        catalog.register_memory("default", self._memory)
        for tool in self.tools:
            catalog.register_tool(tool)

        return ReActAgent(
            name=self.name,
            description=f"Agent: {self.name}",
            catalog=catalog,
            system_instructions=self.instructions,
            max_iterations=self.max_iterations,
            verbose=False,
            response_schema=self.response_schema,
        )

    async def run(self, input: str, **kwargs: Any) -> AgentRunResult:
        """Run the agent with the given input and return the full result.

        Args:
            input: The user message to send to the agent.
            **kwargs: Additional kwargs forwarded to ``ReActAgent.run()``.

        Returns:
            ``AgentRunResult`` with ``final_output``, ``steps``, ``usage``, etc.
        """
        agent = self._build_agent()
        return await agent.run(input, **kwargs)

    async def run_stream(self, input: str, **kwargs: Any) -> AsyncIterator[Any]:
        """Stream the agent's response.

        Yields ``TextDeltaChunk``, ``ReasoningDeltaChunk``, ``CompletionChunk``,
        and ``StructuredOutputChunk`` objects.

        Args:
            input: The user message to send to the agent.
            **kwargs: Additional kwargs forwarded to ``ReActAgent.run_stream()``.
        """
        agent = self._build_agent()
        async for chunk in agent.run_stream(input, **kwargs):
            yield chunk

    @property
    def model_client(self) -> BaseModelClient:
        """Access the underlying model client."""
        return self._model_client

    async def batch(
        self,
        inputs: List[str],
        *,
        config: Optional[BatchConfig] = None,
        **kwargs: Any,
    ) -> BatchResult:
        """Run the agent on multiple inputs concurrently.

        Each input gets its own ``ReActAgent`` instance (fresh context/memory)
        so runs are fully isolated.

        Args:
            inputs: List of user messages to process in parallel.
            config: ``BatchConfig`` controlling concurrency, retries, etc.
                Defaults to ``max_concurrency=5, max_retries=2``.
            **kwargs: Additional kwargs forwarded to ``ReActAgent.run()``.

        Returns:
            ``BatchResult`` with per-item results accessible via ``.items``.

        Example::

            agent = Agent(name="Summariser", model="gpt-5-mini")
            result = await agent.batch(["Summarise doc A", "Summarise doc B"])
            for item in result.items:
                print(item.output.output_text if item.success else item.error)
        """
        batch_config = config or BatchConfig(max_concurrency=5)

        async def _run_single(text: str) -> AgentRunResult:
            agent = self._build_agent()
            return await agent.run(text, **kwargs)

        processor = BatchProcessor(fn=_run_single, config=batch_config)
        return await processor.run(inputs)
