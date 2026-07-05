"""Serving factory — constructs agents, tools, and runtime for the HTTP shell.

This is the ONLY place in the codebase where serving/ and agents/capabilities
meet.  Serving calls these factory functions; it never imports concrete agent
or capability types directly.

infrastructure/ is orthogonal to the 4-layer stack so cross-layer imports
from substrate.agents and substrate.capabilities are permitted here.
"""

from __future__ import annotations

import os
import uuid
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any, List, Optional, cast

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from substrate.config import SubstrateConfig
from substrate.kernel.llm import EmbeddingClient, LLMClient
from substrate.kernel.storage.history import HistoryProvider
from substrate.kernel.tools import (
    Tool,
    ToolRisk,
    is_hosted_tool,
    is_provider_defined_tool,
)
from substrate.logger import setup_logging

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


def init_llm_clients(cfg: SubstrateConfig) -> LLMClients:
    """Create LLM model client, embedding client, and related config."""
    from substrate.integrations.llm.factory import (
        CHAT_MODEL_FALLBACKS,
        create_embedding_client,
        create_model_client,
        resolve_model_for_available_credentials,
    )

    api_keys = {
        "openai": cfg.OPENAI_API_KEY,
        "groq": cfg.GROQ_API_KEY,
        "anthropic": cfg.ANTHROPIC_API_KEY,
        "google": cfg.GEMINI_API_KEY,
        "openrouter": cfg.OPENROUTER_API_KEY,
        "nvidia": cfg.NVIDIA_API_KEY,
    }
    model_client_kwargs = {
        "openai_base_url": cfg.OPENAI_BASE_URL or None,
        "groq_base_url": cfg.GROQ_BASE_URL or None,
        "openrouter_base_url": cfg.OPENROUTER_BASE_URL or None,
        "openrouter_site_url": cfg.OPENROUTER_SITE_URL or None,
        "openrouter_app_name": cfg.OPENROUTER_APP_NAME or None,
        "default_stt_model": cfg.STT_MODEL,
        "default_tts_model": cfg.TTS_MODEL,
        "realtime_model": cfg.REALTIME_MODEL,
    }
    startup_chat_model = resolve_model_for_available_credentials(
        cfg.CHAT_MODEL,
        api_keys=api_keys,
        fallback_models=CHAT_MODEL_FALLBACKS,
    )
    if startup_chat_model != cfg.CHAT_MODEL:
        logger.warning(
            "Chat model %s unavailable with current credentials; falling back to %s",
            cfg.CHAT_MODEL,
            startup_chat_model,
        )
    model_client = create_model_client(
        startup_chat_model, api_keys=api_keys, **model_client_kwargs
    )
    embedding_client = create_embedding_client(cfg.EMBEDDING_MODEL, api_keys=api_keys)
    return LLMClients(
        api_keys=api_keys,
        model_client_kwargs=model_client_kwargs,
        model_client=model_client,
        chat_model=startup_chat_model,
        embedding_client=embedding_client,
    )


# ── Runtime ───────────────────────────────────────────────────────────────────


async def init_runtime(cfg: SubstrateConfig) -> tuple[Any, AsyncExitStack | None]:
    """Build the agent runtime per ``cfg.RUNTIME_BACKEND``.

    Returns ``(runtime, stack_or_None)`` — caller must close the stack on shutdown.
    """
    if cfg.RUNTIME_BACKEND.lower() == "postgres":
        from substrate.infrastructure.runtime import build_postgres_runtime

        pg_url = (cfg.ASYNC_DATABASE_URL or cfg.DATABASE_URL).replace("+asyncpg", "")
        stack = AsyncExitStack()
        runtime = await stack.enter_async_context(
            build_postgres_runtime(
                postgres_url=pg_url,
                reclaim_orphans=True,
                pool_min_size=cfg.RUNTIME_PG_POOL_MIN_SIZE,
                pool_max_size=cfg.RUNTIME_PG_POOL_MAX_SIZE,
            )
        )
        logger.info("Agent runtime: durable (Postgres EventLog)")
        return runtime, stack

    from substrate.agents.runtime import Runtime

    runtime = Runtime()
    await runtime.start()
    logger.info("Agent runtime: in-memory")
    return runtime, None


# ── Infrastructure ────────────────────────────────────────────────────────────


def _init_file_store(cfg: SubstrateConfig) -> Any:
    if cfg.FILE_STORE_BACKEND == "s3":
        from substrate.capabilities.storage.s3 import S3FileStore

        return S3FileStore(
            endpoint_url=cfg.FILE_STORE_ENDPOINT or "",
            access_key=cfg.FILE_STORE_ACCESS_KEY or "",
            secret_key=cfg.FILE_STORE_SECRET_KEY or "",
            bucket=cfg.FILE_STORE_BUCKET,
            region=cfg.FILE_STORE_REGION,
        )
    from substrate.agents.storage.memory import InMemoryFileStore

    return InMemoryFileStore()


async def init_infrastructure(
    cfg: SubstrateConfig,
    embedding_client: EmbeddingClient,
    *,
    engine: AsyncEngine,
    session_factory: async_sessionmaker,
) -> Infrastructure:
    """Create Redis, runtime, file store, vector store, RAG, and bridge registry.

    ``engine`` and ``session_factory`` come from ``init_db()`` called in lifespan.
    """
    import redis.asyncio as aioredis

    from substrate.capabilities.history.redis_history import RedisHistoryProvider
    from substrate.capabilities.knowledge.pipeline import RAGPipeline
    from substrate.capabilities.pipeline.data_ref import DataRefStore
    from substrate.capabilities.tools.skills._manager import SkillManager
    from substrate.capabilities.vector.pgvector_store import PgVectorStore
    from substrate.serving.monolith.sse.bridge import BridgeRegistry

    history = RedisHistoryProvider(
        redis_url=cfg.REDIS_URL,
        ttl=cfg.REDIS_SESSION_TTL,
        max_messages=cfg.SESSION_MAX_MESSAGES,
    )
    await history.connect()

    redis_client = aioredis.from_url(cfg.REDIS_URL, decode_responses=True)

    runtime, runtime_stack = await init_runtime(cfg)

    if cfg.RUNTIME_BACKEND.lower() == "postgres":
        from substrate.agents.storage.tasks import GlobalTaskStore
        from substrate.infrastructure.storage.pg_task_store import PgTaskStore

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

    data_store = DataRefStore(redis_url=cfg.REDIS_URL)
    await data_store.connect()

    bridge_registry = BridgeRegistry(
        response_timeout=300.0,
        signal_bus=runtime.signal_bus,
        scheduler=runtime.scheduler,
    )
    skill_manager = SkillManager(auto_discover=True)
    file_store = _init_file_store(cfg)
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
    cfg: SubstrateConfig,
    *,
    session_factory: Any,
    bridge_registry: Any,
    redis_client: Any = None,
) -> ToolboxResult:
    """Create all tools and return a registry."""
    from substrate.agents.storage.tasks import GlobalTaskStore
    from substrate.agents.tools.toolbox import Toolbox
    from substrate.capabilities.tools.code_interpreter import (
        CodeInterpreterClient,
        K8sSandboxCodeInterpreterTool,
    )
    from substrate.capabilities.tools.human_input import AskHumanTool
    from substrate.capabilities.tools.task_manager.tool import TaskManagerTool
    from substrate.capabilities.tools import (
        CalculatorTool,
        CurrentTimeTool,
    )
    from substrate.capabilities.tools.web.read_url import ReadUrlTool
    from substrate.capabilities.tools.web.search import WebSearchTool

    async def _board_event_sink(conversation_id: str, board: dict) -> None:
        # Subagent boards run in a separate run whose events never reach the
        # parent stream; push them onto the thread bridge so they stream live.
        await bridge_registry.emit(
            conversation_id,
            {
                "type": "tool.result",
                "tool_name": "manage_tasks",
                "structured_content": {"task_list": board},
            },
        )

    task_tool = TaskManagerTool(
        store=GlobalTaskStore.get(), event_sink=_board_event_sink
    )
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
        from substrate.capabilities.tools.code_interpreter.tool import (
            CodeInterpreterTool,
        )

        code_interpreter_tool = CodeInterpreterTool()

    registry = Toolbox()
    # AskHumanTool.execute() genuinely requires the full RunContext (ctx.uuid(),
    # ctx.sleep_until_signal(), ...) to suspend/resume for HITL, not just the
    # kernel's minimal RunMeta that the generic Tool protocol promises. Safe
    # here because this registry is only ever dispatched via ToolInvoker,
    # which always passes the real RunContext — see agents/tools/invoker.py.
    registry.add(ask_tool)  # pyright: ignore[reportArgumentType]
    registry.add(task_tool)
    _exa_key = cfg.EXA_API_KEY or None
    _tavily_key = cfg.TAVILY_API_KEY or None
    registry.add(
        WebSearchTool(
            exa_api_key=_exa_key,
            tavily_api_key=_tavily_key,
            max_results=cfg.WEB_SEARCH_MAX_RESULTS,
            max_chars=cfg.WEB_SEARCH_MAX_CHARS,
        )
    )
    registry.add(
        ReadUrlTool(
            tavily_api_key=_tavily_key,
            exa_api_key=_exa_key,
            max_chars=cfg.WEB_READ_MAX_CHARS,
        )
    )
    registry.add(CalculatorTool())
    registry.add(CurrentTimeTool())
    if code_interpreter_tool:
        registry.add(code_interpreter_tool)

    from substrate.capabilities.tools.utils.tool_search import ToolSearchTool

    local_tools = [
        cast(Tool, t)
        for t in registry.all()
        if not is_hosted_tool(t) and not is_provider_defined_tool(t)
    ]
    registry.add(ToolSearchTool(local_tools))

    tools_requiring_approval = [
        t.name for t in registry.by_risk(ToolRisk.CRITICAL) if t.name != "ask_human"
    ]
    if cfg.DISABLE_TOOL_APPROVALS:
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
    cfg: SubstrateConfig,
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
    from substrate.agents.tools.invoker import ToolInvoker
    from substrate.capabilities.pipeline.data_ref import DataRefArtifactStore
    from substrate.capabilities.pipeline.engine import PipelineEngine
    from substrate.capabilities.pipeline.store import PipelineStore
    from substrate.capabilities.tools.chain.bridge import ChainBridgeRegistry
    from substrate.capabilities.tools.chain.tool import ToolChainTool
    from substrate.capabilities.tools.pipeline_manager import PipelineManagerTool
    from substrate.capabilities.triggers.conditions import ConditionMonitor
    from substrate.capabilities.triggers.scheduler import TriggerScheduler
    from substrate.capabilities.triggers.webhooks import WebhookRegistry
    from substrate.integrations.events.redis_event_bus import EventBus
    from substrate.kernel.tools.chain import ChainPolicy

    pipeline_engine = PipelineEngine(registry=registry, data_store=data_store)
    pipeline_store = PipelineStore(session_factory=session_factory)

    trigger_scheduler = TriggerScheduler(redis_url=cfg.REDIS_URL, runtime=runtime)
    webhook_registry = WebhookRegistry(runtime=runtime)
    condition_monitor = ConditionMonitor(runtime=runtime)

    event_bus = EventBus(redis_url=cfg.REDIS_URL)
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

    import substrate
    from substrate.agents.factory import rebuild_agent
    from substrate.kernel.runtime.ids import RunStatus
    from substrate.kernel.runtime.log_entry import RunLogEntry

    specs = await scheduler.pending_run_specs()
    if not specs:
        return 0

    for run_id, agent_id, spec in specs:
        spec_version = spec.get("agent_version")
        if spec_version != substrate.__version__:
            # The code that would replay this run's effects has moved on
            # since the spec was persisted — resuming anyway risks the
            # replayed effect path silently diverging from what actually
            # happened (a tool renamed/removed, prompt logic changed, etc).
            # Fail the run cleanly rather than attempt a divergent replay.
            logger.warning(
                "Refusing to resume agent %s for run %s: spec version %r != "
                "running version %r",
                agent_id,
                run_id,
                spec_version,
                substrate.__version__,
            )
            error = (
                f"agent version mismatch: spec was persisted at version "
                f"{spec_version!r}, running version is {substrate.__version__!r}"
            )
            final_seq = await runtime.event_log.last_seq(run_id)
            await runtime.event_log.append(
                run_id,
                RunLogEntry(
                    run_id=run_id,
                    seq=final_seq + 1,
                    kind="run.failed",
                    payload={"error": error, "status": "version_mismatch"},
                ),
                expected_seq=final_seq,
            )
            if hasattr(scheduler, "fail_pending_run"):
                await scheduler.fail_pending_run(run_id)
            await runtime.supervisor.finish_run(run_id, RunStatus.FAILED, error=error)
            continue

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
    thread_id: uuid.UUID,
    *,
    model_client: LLMClient,
    tools: List[Tool],
    system_instructions: str,
    cfg: SubstrateConfig,
    history: Optional[HistoryProvider] = None,
    model_context_window: int = 40,
    max_iterations: int = 30,
    runtime: Any = None,
    initial_tool_choice: str | None = None,
) -> Any:
    """Build and register a kernel Agent for this thread.

    Returns a ``ReActAgent`` or ``OrchestratorAgent``.  Serving code
    must treat the return type as ``Any``; the concrete type lives in
    agents/ and must not be imported from serving/.

    Agent topology (the fixed researcher/calculator/clock orchestrator, and
    the default single-assistant shape) lives in ``agents/factory.py`` —
    this function only decides which one to build from ``cfg.AGENT_MODE``
    and registers the result(s) with ``runtime``. ``cfg`` is passed in
    rather than imported from ``substrate.serving.*`` — this module
    (``infrastructure/``) must not reach into ``serving/``, only the reverse.

    Cold-store memory-seeding reads straight off ``runtime``'s EventLog
    (``agents.factory.step_rows_from_log`` — the EventLog is the single
    source of truth for conversation history, not a separate steps table;
    see ``serving/stream/history.py::project_thread()``, the sibling
    projection for UI display) rather than taking an injected loader
    callback — the monolith has exactly one cold-store mechanism now.
    """
    from substrate.agents.factory import (
        build_research_orchestrator,
        build_token_budget_pipeline,
        create_assistant_agent,
        load_session_memory,
        step_rows_from_log,
    )

    if runtime is None:
        raise ValueError("build_agent_for_thread() requires a runtime.")

    session_id = str(thread_id)
    memory = await load_session_memory(
        session_id=session_id,
        system_instructions=system_instructions,
        history=history,
        include_mcp_app_context=True,
        cold_store_name="EventLog",
        load_persisted_steps=lambda: step_rows_from_log(
            runtime.event_log, runtime.scheduler, session_id
        ),
    )

    if cfg.AGENT_MODE.lower() == "orchestrator":
        from substrate.capabilities.tools import CalculatorTool, CurrentTimeTool
        from substrate.capabilities.tools.web.read_url import ReadUrlTool
        from substrate.capabilities.tools.web.search import WebSearchTool

        exa_api_key = cfg.EXA_API_KEY or None
        tavily_api_key = cfg.TAVILY_API_KEY or None
        research = build_research_orchestrator(
            model_client=model_client,
            researcher_tools=[
                WebSearchTool(exa_api_key=exa_api_key, tavily_api_key=tavily_api_key),
                ReadUrlTool(tavily_api_key=tavily_api_key, exa_api_key=exa_api_key),
            ],
            calculator_tools=[CalculatorTool()],
            clock_tools=[CurrentTimeTool()],
        )
        for agent in research.all_agents:
            await runtime.register(agent)
        return research.coordinator

    agent = create_assistant_agent(
        name="assistant",
        session_id=session_id,
        model_client=model_client,
        tools=tools,
        system_instructions=system_instructions,
        memory=memory,
        model_context=build_token_budget_pipeline(),
        max_iterations=max_iterations,
        initial_tool_choice=initial_tool_choice,
    )
    await runtime.register(agent)
    return agent


def build_chat_tools(toolbox: Any, bridge: Any) -> list[Any]:
    """Return the per-request tool list with AskHumanTool wired to the bridge.

    ``toolbox`` is the shared Toolbox from ``app.state.tools``.
    ``bridge``  is the per-thread WebHITLBridge.

    Serving calls this instead of importing AskHumanTool and WebSurferTool directly.
    """
    from substrate.capabilities.tools.human_input import AskHumanTool
    from substrate.capabilities.tools.web.search import WebSearchTool
    from substrate.capabilities.tools.web.surfer import WebSurferTool

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
    from substrate.capabilities.history.redis_history import RedisHistoryProvider

    provider = RedisHistoryProvider(redis_url=redis_url)
    await provider.connect()
    return provider


def build_runtime_default_tools() -> list[Any]:
    """Build the default tool list for the agent_runtime microservice."""
    tools: list[Any] = []
    try:
        from substrate.capabilities.tools.web.surfer import WebSurferTool

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
    from substrate.agents.context import CompactionPipeline, SlidingWindowCompaction
    from substrate.agents.factory import create_assistant_agent

    return create_assistant_agent(
        model_client=model_client,
        tools=tools,
        system_instructions=system_instructions,
        memory=memory,
        model_context=CompactionPipeline(
            [SlidingWindowCompaction(max_messages=model_context_window)]
        ),
        max_iterations=max_iterations,
        name="assistant",
        session_id=session_id,
    )
