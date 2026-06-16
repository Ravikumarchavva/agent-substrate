"""Serving factory — constructs agents, tools, and runtime for the HTTP shell.

This is the ONLY place in the codebase where serving/ and agents/capabilities
meet.  Serving calls these factory functions; it never imports concrete agent
or capability types directly.

infrastructure/ is orthogonal to the 4-layer stack so cross-layer imports
from ravi.agents and ravi.capabilities are permitted here.
"""

from __future__ import annotations

import os
import uuid
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any, List, Optional

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from ravi.config import Settings
from ravi.kernel.llm import EmbeddingClient, LLMClient
from ravi.kernel.storage.history import HistoryProvider
from ravi.kernel.tools import Tool, ToolRisk
from ravi.logger import setup_logging

logger = setup_logging()


# ── Return containers ─────────────────────────────────────────────────────────


@dataclass
class LLMClients:
    api_keys: dict[str, str]
    model_client_kwargs: dict[str, Any]
    model_client: LLMClient
    chat_model: str
    embedding_client: EmbeddingClient


@dataclass
class Infrastructure:
    history: Any
    redis_client: Any
    runtime: Any
    session_factory: async_sessionmaker
    vector_store: Any
    rag_pipeline: Any
    data_store: Any
    bridge_registry: Any
    skill_manager: Any
    file_store: Any
    runtime_stack: AsyncExitStack | None = None


@dataclass
class ToolboxResult:
    registry: Any
    task_tool: Any
    ask_tool: Any
    ci_client: Optional[Any]
    code_interpreter_tool: Optional[Tool]
    tools_requiring_approval: list[str]


@dataclass
class RuntimeServices:
    chain_bridge_registry: Any
    pipeline_engine: Any
    pipeline_store: Any
    trigger_scheduler: Any
    webhook_registry: Any
    condition_monitor: Any


# ── LLM clients ───────────────────────────────────────────────────────────────


def init_llm_clients(settings: Settings) -> LLMClients:
    """Create LLM model client, embedding client, and related config."""
    from ravi.integrations.llm.factory import (
        CHAT_MODEL_FALLBACKS,
        create_embedding_client,
        create_model_client,
        resolve_model_for_available_credentials,
    )

    api_keys = {
        "openai": settings.OPENAI_API_KEY,
        "groq": settings.GROQ_API_KEY,
        "anthropic": settings.ANTHROPIC_API_KEY,
        "google": settings.GEMINI_API_KEY,
        "openrouter": settings.OPENROUTER_API_KEY,
    }
    model_client_kwargs = {
        "openai_base_url": settings.OPENAI_BASE_URL or None,
        "groq_base_url": settings.GROQ_BASE_URL or None,
        "openrouter_base_url": settings.OPENROUTER_BASE_URL or None,
        "openrouter_site_url": settings.OPENROUTER_SITE_URL or None,
        "openrouter_app_name": settings.OPENROUTER_APP_NAME or None,
        "default_stt_model": settings.STT_MODEL,
        "default_tts_model": settings.TTS_MODEL,
        "realtime_model": settings.REALTIME_MODEL,
    }
    startup_chat_model = resolve_model_for_available_credentials(
        settings.CHAT_MODEL,
        api_keys=api_keys,
        fallback_models=CHAT_MODEL_FALLBACKS,
    )
    if startup_chat_model != settings.CHAT_MODEL:
        logger.warning(
            "Chat model %s unavailable with current credentials; falling back to %s",
            settings.CHAT_MODEL,
            startup_chat_model,
        )
    model_client = create_model_client(
        startup_chat_model, api_keys=api_keys, **model_client_kwargs
    )
    embedding_client = create_embedding_client(
        settings.EMBEDDING_MODEL, api_keys=api_keys
    )
    return LLMClients(
        api_keys=api_keys,
        model_client_kwargs=model_client_kwargs,
        model_client=model_client,
        chat_model=startup_chat_model,
        embedding_client=embedding_client,
    )


# ── Runtime ───────────────────────────────────────────────────────────────────


async def init_runtime(settings: Settings) -> tuple[Any, AsyncExitStack | None]:
    """Build the agent runtime per ``settings.RUNTIME_BACKEND``.

    Returns ``(runtime, stack_or_None)`` — caller must close the stack on shutdown.
    """
    if settings.RUNTIME_BACKEND.lower() == "postgres":
        from ravi.infrastructure.runtime import build_postgres_runtime

        pg_url = (settings.ASYNC_DATABASE_URL or settings.DATABASE_URL).replace(
            "+asyncpg", ""
        )
        stack = AsyncExitStack()
        runtime = await stack.enter_async_context(
            build_postgres_runtime(
                postgres_url=pg_url,
                redis_url=settings.REDIS_URL,
                reclaim_orphans=True,
            )
        )
        logger.info("Agent runtime: durable (Postgres EventLog + Redis journal)")
        return runtime, stack

    from ravi.agents.runtime import Runtime

    runtime = Runtime()
    await runtime.start()
    logger.info("Agent runtime: in-memory")
    return runtime, None


# ── Infrastructure ────────────────────────────────────────────────────────────


def _init_file_store(settings: Settings) -> Any:
    if settings.FILE_STORE_BACKEND == "s3":
        from ravi.capabilities.storage.s3 import S3FileStore

        return S3FileStore(
            endpoint_url=settings.FILE_STORE_ENDPOINT or "",
            access_key=settings.FILE_STORE_ACCESS_KEY or "",
            secret_key=settings.FILE_STORE_SECRET_KEY or "",
            bucket=settings.FILE_STORE_BUCKET,
            region=settings.FILE_STORE_REGION,
        )
    from ravi.agents.storage.memory import InMemoryFileStore

    return InMemoryFileStore()


async def init_infrastructure(
    settings: Settings,
    embedding_client: EmbeddingClient,
    *,
    engine: AsyncEngine,
    session_factory: async_sessionmaker,
) -> Infrastructure:
    """Create Redis, runtime, file store, vector store, RAG, and bridge registry.

    ``engine`` and ``session_factory`` come from ``init_db()`` called in lifespan.
    """
    import redis.asyncio as aioredis

    from ravi.capabilities.history.redis_history import RedisHistoryProvider
    from ravi.capabilities.knowledge.pipeline import RAGPipeline
    from ravi.capabilities.pipeline.data_ref import DataRefStore
    from ravi.capabilities.tools.skills._manager import SkillManager
    from ravi.capabilities.vector.pgvector_store import PgVectorStore
    from ravi.serving.monolith.sse.bridge import BridgeRegistry

    history = RedisHistoryProvider(
        redis_url=settings.REDIS_URL,
        ttl=settings.REDIS_SESSION_TTL,
        max_messages=settings.SESSION_MAX_MESSAGES,
    )
    await history.connect()

    redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)

    runtime, runtime_stack = await init_runtime(settings)

    if settings.RUNTIME_BACKEND.lower() == "postgres":
        from ravi.agents.storage.tasks import GlobalTaskStore
        from ravi.infrastructure.storage.pg_task_store import PgTaskStore

        pg_task_store = PgTaskStore(session_factory)
        await pg_task_store.setup()
        GlobalTaskStore.set(pg_task_store)  # type: ignore[arg-type]
        logger.info("Task store: durable (Postgres JSONB)")

    vector_store = PgVectorStore(
        session_factory=session_factory,
        engine=engine,
        dimensions=1536,
    )
    rag_pipeline = RAGPipeline(
        embedding_client=embedding_client,
        vector_store=vector_store,
    )

    data_store = DataRefStore(redis_url=settings.REDIS_URL)
    await data_store.connect()

    bridge_registry = BridgeRegistry(response_timeout=300.0)
    skill_manager = SkillManager(auto_discover=True)
    file_store = _init_file_store(settings)
    await file_store.connect()

    return Infrastructure(
        history=history,
        redis_client=redis_client,
        runtime=runtime,
        session_factory=session_factory,
        vector_store=vector_store,
        rag_pipeline=rag_pipeline,
        data_store=data_store,
        bridge_registry=bridge_registry,
        skill_manager=skill_manager,
        file_store=file_store,
        runtime_stack=runtime_stack,
    )


# ── Tool registry ─────────────────────────────────────────────────────────────


async def init_tool_registry(
    settings: Settings,
    *,
    session_factory: Any,
    bridge_registry: Any,
    redis_client: Any = None,
) -> ToolboxResult:
    """Create all tools and return a registry."""
    from ravi.agents.storage.tasks import GlobalTaskStore
    from ravi.agents.tools.toolbox import Toolbox
    from ravi.capabilities.tools.code_interpreter import (
        CodeInterpreterClient,
        K8sSandboxCodeInterpreterTool,
    )
    from ravi.capabilities.tools.human_input import AskHumanTool
    from ravi.capabilities.tools.task_manager.tool import TaskManagerTool
    from ravi.capabilities.tools import (
        CalculatorTool,
        CurrentTimeTool,
        ReadUrlTool,
        WebSearchTool,
    )

    task_tool = TaskManagerTool(store=GlobalTaskStore.get())
    ask_tool = AskHumanTool(handler=None, max_requests_per_run=5)  # type: ignore[arg-type]

    code_interpreter_tool: Tool | None = None
    ci_client: CodeInterpreterClient | None = None

    try:
        code_interpreter_tool = K8sSandboxCodeInterpreterTool(
            template=os.environ.get("CI_SANDBOX_TEMPLATE", "python-sandbox-template"),
            namespace=os.environ.get("CI_SANDBOX_NAMESPACE", "default"),
        )
        logger.info("Kubernetes agent-sandbox Code Interpreter registered")
    except Exception as exc:
        logger.warning(
            "K8sSandboxCodeInterpreterTool unavailable (%s); using fallback", exc
        )
        from ravi.capabilities.tools.code_interpreter.tool import CodeInterpreterTool

        code_interpreter_tool = CodeInterpreterTool()

    registry = Toolbox()
    registry.add(ask_tool)
    registry.add(task_tool)
    registry.add(WebSearchTool())
    registry.add(ReadUrlTool())
    registry.add(CalculatorTool())
    registry.add(CurrentTimeTool())
    if code_interpreter_tool:
        registry.add(code_interpreter_tool)

    from ravi.capabilities.tools.utils.tool_search import ToolSearchTool

    registry.add(ToolSearchTool(registry.all()))

    tools_requiring_approval = [
        t.name for t in registry.by_risk(ToolRisk.CRITICAL) if t.name != "ask_human"
    ]
    if settings.DISABLE_TOOL_APPROVALS:
        tools_requiring_approval = []

    return ToolboxResult(
        registry=registry,
        task_tool=task_tool,
        ask_tool=ask_tool,
        ci_client=ci_client,
        code_interpreter_tool=code_interpreter_tool,
        tools_requiring_approval=tools_requiring_approval,
    )


# ── Runtime services ──────────────────────────────────────────────────────────


async def init_runtime_services(
    settings: Settings,
    *,
    registry: Any,
    data_store: Any,
    session_factory: Any,
    runtime: Any,
    tools_requiring_approval: list[str],
    tool_timeout: float,
    code_interpreter_tool: Any | None = None,
) -> RuntimeServices:
    """Create ToolChainTool, pipeline engine, triggers."""
    from ravi.agents.tools.invoker import ToolInvoker
    from ravi.capabilities.pipeline.data_ref import DataRefArtifactStore
    from ravi.capabilities.pipeline.engine import PipelineEngine
    from ravi.capabilities.pipeline.store import PipelineStore
    from ravi.capabilities.tools.chain.bridge import ChainBridgeRegistry
    from ravi.capabilities.tools.chain.tool import ToolChainTool
    from ravi.capabilities.tools.pipeline_manager import PipelineManagerTool
    from ravi.capabilities.triggers.conditions import ConditionMonitor
    from ravi.capabilities.triggers.scheduler import TriggerScheduler
    from ravi.capabilities.triggers.webhooks import WebhookRegistry
    from ravi.integrations.events.redis_event_bus import EventBus
    from ravi.kernel.tools.chain import ChainPolicy

    pipeline_engine = PipelineEngine(registry=registry, data_store=data_store)
    pipeline_store = PipelineStore(session_factory=session_factory)

    trigger_scheduler = TriggerScheduler(redis_url=settings.REDIS_URL, runtime=runtime)
    webhook_registry = WebhookRegistry(runtime=runtime)
    condition_monitor = ConditionMonitor(runtime=runtime)

    event_bus = EventBus(redis_url=settings.REDIS_URL)
    condition_monitor.set_event_bus(event_bus)

    try:
        await trigger_scheduler.start()
    except Exception as exc:
        logger.warning("TriggerScheduler failed to start: %s", exc)

    try:
        await condition_monitor.start()
    except Exception as exc:
        logger.warning("ConditionMonitor failed to start: %s", exc)

    chain_bridge_registry = ChainBridgeRegistry()
    bridge_base_url = os.environ.get("CHAIN_BRIDGE_URL", "http://localhost:8001")
    if code_interpreter_tool is not None:
        artifact_store = DataRefArtifactStore(data_store)
        invoker = ToolInvoker(
            registry=registry,
            artifact_store=artifact_store,
            policy=ChainPolicy(),
        )
        try:
            tool_chain = ToolChainTool(
                invoker=invoker,
                interpreter=code_interpreter_tool,
                bridge_registry=chain_bridge_registry,
                bridge_base_url=bridge_base_url,
            )
            registry.add(tool_chain)
            logger.info(
                "ToolChainTool registered (sandboxed code-mode chaining enabled)"
            )
        except RuntimeError as exc:
            logger.warning("ToolChainTool not registered: %s", exc)
    else:
        logger.info("ToolChainTool not registered (CodeInterpreter unavailable)")

    pipeline_manager_tool = PipelineManagerTool(
        pipeline_engine=pipeline_engine,
        pipeline_store=pipeline_store,
    )
    registry.add(pipeline_manager_tool)

    return RuntimeServices(
        chain_bridge_registry=chain_bridge_registry,
        pipeline_engine=pipeline_engine,
        pipeline_store=pipeline_store,
        trigger_scheduler=trigger_scheduler,
        webhook_registry=webhook_registry,
        condition_monitor=condition_monitor,
    )


# ── Cold resume ───────────────────────────────────────────────────────────────


async def resume_pending_runs(runtime: Any, *, registry: Any, model_client: Any) -> int:
    """Register rebuilt agents for pending runs that have a persisted spec."""
    scheduler = getattr(runtime, "_scheduler", None)
    if scheduler is None or not hasattr(scheduler, "pending_run_specs"):
        return 0

    from ravi.agents.factory import rebuild_agent

    specs = await scheduler.pending_run_specs()
    if not specs:
        return 0

    for _run_id, agent_id, spec in specs:
        tool_names: list[str] = spec.get("tool_names") or []
        resolved_tools = [
            t for t in registry.all() if getattr(t, "name", None) in tool_names
        ]
        try:
            agent = rebuild_agent(spec, model_client=model_client, tools=resolved_tools)
            agent.id = agent_id  # type: ignore[attr-defined]
            await runtime.register(agent)
            logger.info("Resumed agent %s for pending run", agent_id)
        except Exception as exc:
            logger.warning("Failed to resume agent %s: %s", agent_id, exc)

    return len(specs)


# ── Agent construction ────────────────────────────────────────────────────────


async def build_agent_for_thread(
    db: AsyncSession,
    thread_id: uuid.UUID,
    *,
    model_client: LLMClient,
    tools: List[Tool],
    system_instructions: str,
    history: Optional[HistoryProvider] = None,
    model_context_window: int = 40,
    max_iterations: int = 30,
    runtime: Any = None,
) -> Any:
    """Build and register a kernel Agent for this thread.

    Returns a ``ReActAgent`` or ``OrchestratorAgent``.  Serving code
    must treat the return type as ``Any``; the concrete type lives in
    agents/ and must not be imported from serving/.
    """
    from ravi.agents.context import (
        CompactionPipeline,
        ContextConfig,
        InMemoryHistoryProvider,
        SlidingWindowCompaction,
    )
    from ravi.agents.core import OrchestratorAgent, ReActAgent, SubAgentConfig
    from ravi.agents.factory import load_session_memory
    from ravi.agents.tools.toolbox import Toolbox
    from ravi.capabilities.tools import (
        CalculatorTool,
        CurrentTimeTool,
        ReadUrlTool,
        WebSearchTool,
    )
    from ravi.config import settings as _settings

    if runtime is None:
        raise ValueError("build_agent_for_thread() requires a runtime.")

    def _make_context(max_messages: int = 20) -> ContextConfig:
        return ContextConfig(
            InMemoryHistoryProvider(),
            pipeline=CompactionPipeline(
                [SlidingWindowCompaction(max_messages=max_messages)]
            ),
        )

    from ravi.serving.monolith.services import load_messages_for_memory

    session_id = str(thread_id)
    memory = await load_session_memory(
        session_id=session_id,
        system_instructions=system_instructions,
        history=history,
        include_mcp_app_context=True,
        cold_store_name="Postgres",
        load_persisted_steps=lambda: load_messages_for_memory(db, thread_id),
    )

    if _settings.AGENT_MODE.lower() == "orchestrator":

        def _registry(*tool_instances) -> Toolbox:
            tb = Toolbox()
            for t in tool_instances:
                tb.register(t)
            return tb

        researcher = ReActAgent(
            "researcher",
            model=model_client,
            tools=_registry(WebSearchTool(), ReadUrlTool()),
            context=_make_context(model_context_window),
            system_instructions="You are a research specialist.",
            max_iterations=5,
        )
        calculator = ReActAgent(
            "calculator",
            model=model_client,
            tools=_registry(CalculatorTool()),
            context=_make_context(model_context_window),
            system_instructions="You are a calculation specialist.",
            max_iterations=3,
        )
        clock = ReActAgent(
            "clock",
            model=model_client,
            tools=_registry(CurrentTimeTool()),
            context=_make_context(model_context_window),
            system_instructions="You are a time specialist.",
            max_iterations=2,
        )
        orchestrator = OrchestratorAgent(
            "coordinator",
            model=model_client,
            sub_agents=[
                SubAgentConfig(
                    researcher, description="Searches the web.", ask_timeout=60.0
                ),
                SubAgentConfig(
                    calculator, description="Performs calculations.", ask_timeout=30.0
                ),
                SubAgentConfig(
                    clock, description="Reports the current time.", ask_timeout=10.0
                ),
            ],
            max_iterations=15,
            context=_make_context(model_context_window),
        )
        for agent in [researcher, calculator, clock, orchestrator]:
            await runtime.register(agent)
        return orchestrator

    toolbox = Toolbox()
    for t in tools:
        toolbox.add(t)

    agent = ReActAgent(
        f"assistant-{session_id}",
        model=model_client,
        tools=toolbox,
        context=ContextConfig(
            memory,
            pipeline=CompactionPipeline(
                [SlidingWindowCompaction(max_messages=model_context_window)]
            ),
        ),
        system_instructions=system_instructions,
        max_iterations=max_iterations,
    )
    await runtime.register(agent)
    return agent


def build_chat_tools(toolbox: Any, bridge: Any) -> list[Any]:
    """Return the per-request tool list with AskHumanTool wired to the bridge.

    ``toolbox`` is the shared Toolbox from ``app.state.tools``.
    ``bridge``  is the per-thread WebHITLBridge.

    Serving calls this instead of importing AskHumanTool and WebSurferTool directly.
    """
    from ravi.capabilities.tools.human_input import AskHumanTool
    from ravi.capabilities.tools.web.search import WebSearchTool
    from ravi.capabilities.tools.web.surfer import WebSurferTool

    base_tools = [t for t in toolbox.all() if not isinstance(t, AskHumanTool)]
    ask_tool = AskHumanTool(handler=bridge.human_handler, max_requests_per_run=5)
    tools: list[Any] = [ask_tool] + base_tools

    if not any(isinstance(t, WebSearchTool) for t in tools):
        tools.append(WebSearchTool())

    if not any(isinstance(t, WebSurferTool) for t in tools):
        try:
            tools.append(WebSurferTool())
        except Exception:
            logger.debug("WebSurferTool not available for this request")

    return tools


async def build_history_provider(redis_url: str) -> Any:
    """Build and connect a RedisHistoryProvider for the agent_runtime microservice."""
    from ravi.capabilities.history.redis_history import RedisHistoryProvider

    provider = RedisHistoryProvider(redis_url=redis_url)
    await provider.connect()
    return provider


def build_runtime_default_tools() -> list[Any]:
    """Build the default tool list for the agent_runtime microservice."""
    tools: list[Any] = []
    try:
        from ravi.capabilities.tools.web.surfer import WebSurferTool

        tools.append(WebSurferTool())
    except Exception:
        logger.debug("WebSurferTool not available")
    return tools


def build_agent_for_run(
    *,
    model_client: Any,
    tools: list[Any],
    system_instructions: str,
    memory: Any,
    session_id: str | None = None,
    model_context_window: int = 40,
    max_iterations: int = 30,
) -> Any:
    """Create a stateless agent for a microservice agent_runtime run."""
    from ravi.agents.context import CompactionPipeline, SlidingWindowCompaction
    from ravi.agents.factory import create_assistant_agent

    return create_assistant_agent(
        model_client=model_client,
        tools=tools,
        system_instructions=system_instructions,
        memory=memory,
        model_context=CompactionPipeline(
            [SlidingWindowCompaction(max_messages=model_context_window)]
        ),
        max_iterations=max_iterations,
        name=f"assistant-{session_id}" if session_id else "ChatBot",
    )
