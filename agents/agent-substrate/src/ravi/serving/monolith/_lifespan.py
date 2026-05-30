"""Focused initialisation helpers extracted from the monolith lifespan.

Each ``init_*`` function creates and returns a group of related objects.
The ``lifespan()`` in ``app.py`` calls these helpers and assigns the
returned objects to ``app.state.*``.
"""

from __future__ import annotations
from ravi.logger import setup_logging

import os
from dataclasses import dataclass
from typing import Any, Optional

import redis.asyncio as aioredis

from ravi.catalog.tools.code_interpreter import K8sSandboxCodeInterpreterTool
from ravi.catalog.tools.code_interpreter.http_client import (
    CodeInterpreterClient,
)
from ravi.catalog.tools.file_manager.tool import FileManagerTool
from ravi.catalog.tools.human_input.tool import AskHumanTool
from ravi.catalog.tools.task_manager.tool import (
    TaskManagerTool,
    current_thread_id as _task_thread_id,
)
from ravi.config import Settings
from ravi.fabric.llm import LLMClient, BaseEmbeddingClient
from ravi.fabric.runtime.local import LocalRuntime
from ravi.kernel.storage.base import FileStore
from ravi.fabric.storage.factory import create_file_store
from ravi.kernel.tools.base_tool import BaseTool, ToolRisk
from ravi.fabric.tools.builtin_tools import (
    CalculatorTool,
    GetCurrentTimeTool,
    GetBitcoinPriceTool,
)
from ravi.fabric.catalog import AgentCatalogRegistry
from ravi.adapters.llm.factory import (
    CHAT_MODEL_FALLBACKS,
    create_embedding_client,
    create_model_client,
    resolve_model_for_available_credentials,
)
from ravi.adapters.mcp.app_tools import (
    ColorPaletteTool,
    DataVisualizerTool,
    GoogleWorkspaceTool,
    JsonExplorerTool,
    KanbanBoardTool,
    MarkdownPreviewerTool,
    SpotifyPlayerTool,
)
from ravi.adapters.memory.redis_history import RedisHistoryProvider
from ravi.adapters.spotify.client import SpotifyService
from ravi.serving.monolith.database import get_session_factory
from ravi.serving.monolith.sse.bridge import BridgeRegistry

logger = setup_logging()


# ── Return containers ────────────────────────────────────────────────────────


@dataclass
class LLMClients:
    """Objects returned by :func:`init_llm_clients`."""

    api_keys: dict[str, str]
    model_client_kwargs: dict[str, Any]
    model_client: LLMClient
    chat_model: str
    embedding_client: BaseEmbeddingClient


@dataclass
class Infrastructure:
    """Objects returned by :func:`init_infrastructure`."""

    history: RedisHistoryProvider
    redis_client: Any
    runtime: LocalRuntime
    file_store: FileStore
    session_factory: Any
    vector_store: Any
    rag_pipeline: Any
    data_store: Any
    bridge_registry: BridgeRegistry


@dataclass
class ToolRegistryResult:
    """Objects returned by :func:`init_tool_registry`."""

    catalog: AgentCatalogRegistry
    task_tool: TaskManagerTool
    ask_tool: AskHumanTool
    ci_client: Optional[CodeInterpreterClient]
    code_interpreter_tool: Optional[BaseTool]
    tools_requiring_approval: list[str]


@dataclass
class RuntimeServices:
    """Objects returned by :func:`init_runtime`."""

    chain_runtime: Any
    pipeline_engine: Any
    pipeline_store: Any
    workflow_client: Any
    trigger_scheduler: Any
    webhook_registry: Any
    condition_monitor: Any


# ── Initialisation functions ─────────────────────────────────────────────────


def init_llm_clients(settings: Settings) -> LLMClients:
    """Create LLM model client, embedding client, and related config."""

    api_keys = {
        "openai": settings.OPENAI_API_KEY,
        "groq": settings.GROQ_API_KEY,
        "anthropic": settings.ANTHROPIC_API_KEY,
        "google": settings.GOOGLE_API_KEY,
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
            "Chat model %s is unavailable with current credentials; falling back to %s",
            settings.CHAT_MODEL,
            startup_chat_model,
        )
    model_client = create_model_client(
        startup_chat_model,
        api_keys=api_keys,
        **model_client_kwargs,
    )
    embedding_client = create_embedding_client(
        settings.EMBEDDING_MODEL,
        api_keys=api_keys,
    )

    return LLMClients(
        api_keys=api_keys,
        model_client_kwargs=model_client_kwargs,
        model_client=model_client,
        chat_model=startup_chat_model,
        embedding_client=embedding_client,
    )


async def init_infrastructure(
    settings: Settings,
    embedding_client: BaseEmbeddingClient,
) -> Infrastructure:
    """Create Redis, runtime, file store, vector store, RAG, and bridge registry."""

    # Redis — primary session history store for stateless agents
    history = RedisHistoryProvider(
        redis_url=settings.REDIS_URL,
        ttl=settings.REDIS_SESSION_TTL,
        max_messages=settings.SESSION_MAX_MESSAGES,
    )
    await history.connect()

    # Standalone Redis client for non-memory operations (auth token JTIs, etc.)
    redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)

    # Agent runtime — actor-based message dispatch (in-process by default)
    runtime = LocalRuntime()
    await runtime.start()

    # File Store (local / S3 / encrypted)
    file_store = create_file_store(settings)
    await file_store.startup()

    # Session factory (needed by FileManagerTool and routes)
    session_factory = get_session_factory()

    # Vector store + RAG pipeline (pgvector-backed)
    from ravi.adapters.vector.pgvector_store import PgVectorStore
    from ravi.catalog.rag.pipeline import RAGPipeline

    vector_store = PgVectorStore(
        session_factory=session_factory,
        dimensions=1536,
    )
    rag_pipeline = RAGPipeline(
        embedding_client=embedding_client,
        vector_store=vector_store,
    )

    # DataRefStore — zero-context-bloat data exchange (Redis + optional S3)
    from ravi.catalog._data_ref import DataRefStore

    data_store = DataRefStore(redis_url=settings.REDIS_URL)
    await data_store.connect()

    # HITL bridge registry: one WebHITLBridge per active thread (conversation).
    bridge_registry = BridgeRegistry(response_timeout=300.0)

    return Infrastructure(
        history=history,
        redis_client=redis_client,
        runtime=runtime,
        file_store=file_store,
        session_factory=session_factory,
        vector_store=vector_store,
        rag_pipeline=rag_pipeline,
        data_store=data_store,
        bridge_registry=bridge_registry,
    )


async def init_tool_registry(
    settings: Settings,
    *,
    file_store: FileStore,
    session_factory: Any,
    bridge_registry: BridgeRegistry,
    redis_client: Any = None,
) -> ToolRegistryResult:
    """Create all tools, register them in an :class:`AgentCatalogRegistry`."""

    # TaskManagerTool — emitter wired via dynamic closure through bridge_registry
    async def _task_event_emitter(event: dict) -> None:
        """Emit task SSE events to the active bridge for the current thread."""
        tid = _task_thread_id.get("default")
        await bridge_registry.emit(tid, event)

    task_tool = TaskManagerTool(event_emitter=_task_event_emitter)

    # AskHumanTool placeholder (a real per-thread tool is built in _get_agent_deps)
    ask_tool = AskHumanTool(handler=None, max_requests_per_run=5)  # type: ignore[arg-type]

    # Spotify service (optional)
    spotify_svc = None
    if settings.SPOTIFY_CLIENT_ID and settings.SPOTIFY_CLIENT_SECRET:
        spotify_svc = SpotifyService(
            client_id=settings.SPOTIFY_CLIENT_ID,
            client_secret=settings.SPOTIFY_CLIENT_SECRET,
        )

    # Code Interpreter (HTTP client → separate pod)
    code_interpreter_tool: BaseTool | None = None
    ci_client: CodeInterpreterClient | None = None

    try:
        code_interpreter_tool = K8sSandboxCodeInterpreterTool(
            template=os.environ.get("CI_SANDBOX_TEMPLATE", "python-sandbox-template"),
            namespace=os.environ.get("CI_SANDBOX_NAMESPACE", "default"),
        )
        logger.info(
            "Kubernetes agent-sandbox Code Interpreter registered successfully!"
        )
    except Exception as e:
        logger.warning(
            "Failed to initialize K8sSandboxCodeInterpreterTool: %s. Falling back to CodeInterpreterTool.",
            e,
        )
        from ravi.catalog.tools.code_interpreter.tool import CodeInterpreterTool

        code_interpreter_tool = CodeInterpreterTool()

    file_manager_tool = FileManagerTool(
        file_store=file_store,
        session_factory=session_factory,
    )

    # ── Capability Registry ──────────────────────────────────────────────
    catalog = AgentCatalogRegistry()
    catalog.register_tool(
        ask_tool,
        category="communication",
        tags=["human", "input", "question", "approval", "hitl"],
        aliases=["human_input"],
    )
    catalog.register_tool(
        task_tool,
        category="development/project",
        tags=["task", "kanban", "todo", "project", "plan", "track"],
        aliases=["task_manager"],
    )
    catalog.register_tool(
        file_manager_tool,
        category="data/management",
        tags=["file", "upload", "download", "read", "write", "storage"],
        aliases=["file_tool"],
    )
    catalog.register_tool(
        CalculatorTool(),
        category="productivity",
        tags=["math", "calculate", "arithmetic", "expression"],
        aliases=["math_tool"],
    )
    catalog.register_tool(
        GetCurrentTimeTool(),
        category="productivity",
        tags=["time", "date", "timezone", "clock", "now"],
        aliases=["clock"],
    )
    catalog.register_tool(
        GetBitcoinPriceTool(),
        category="productivity",
        tags=["crypto", "bitcoin", "price", "finance"],
        aliases=["btc_price"],
    )
    catalog.register_tool(
        DataVisualizerTool(),
        category="data/visualization",
        tags=["chart", "graph", "plot", "bar", "line", "pie"],
        aliases=["chart_tool", "plot_tool"],
    )
    catalog.register_tool(
        MarkdownPreviewerTool(),
        category="data/visualization",
        tags=["markdown", "preview", "render", "document"],
        aliases=["md_preview"],
    )
    catalog.register_tool(
        JsonExplorerTool(),
        category="data/exploration",
        tags=["json", "tree", "inspect", "parse", "data"],
        aliases=["json_viewer"],
    )
    catalog.register_tool(
        ColorPaletteTool(),
        category="data/visualization",
        tags=["color", "palette", "contrast", "wcag", "harmony"],
        aliases=["color_tool"],
    )
    catalog.register_tool(
        KanbanBoardTool(),
        category="development/project",
        tags=["kanban", "board", "drag", "column", "task"],
        aliases=["project_board"],
    )
    catalog.register_tool(
        SpotifyPlayerTool(spotify_service=spotify_svc),
        category="media",
        tags=["music", "play", "song", "track", "spotify", "stream", "audio"],
        aliases=["music_player"],
    )
    catalog.register_tool(
        GoogleWorkspaceTool(redis_client=redis_client),
        category="productivity",
        tags=[
            "google",
            "workspace",
            "drive",
            "calendar",
            "gmail",
            "email",
            "events",
            "files",
        ],
        aliases=["workspace", "google_drive", "google_calendar", "gmail_tool"],
    )
    if code_interpreter_tool:
        catalog.register_tool(
            code_interpreter_tool,
            category="development/execution",
            tags=["python", "bash", "code", "execute", "run", "script"],
            aliases=["code_exec", "sandbox"],
        )

    # Derive tools requiring approval from risk level
    tools_requiring_approval = [
        e.name for e in catalog.by_risk(ToolRisk.CRITICAL) if e.name != "ask_human"
    ]
    if settings.DISABLE_TOOL_APPROVALS:
        tools_requiring_approval = []

    return ToolRegistryResult(
        catalog=catalog,
        task_tool=task_tool,
        ask_tool=ask_tool,
        ci_client=ci_client,
        code_interpreter_tool=code_interpreter_tool,
        tools_requiring_approval=tools_requiring_approval,
    )


async def init_runtime_services(
    settings: Settings,
    *,
    catalog: AgentCatalogRegistry,
    data_store: Any,
    session_factory: Any,
    runtime: LocalRuntime,
    tools_requiring_approval: list[str],
    tool_timeout: float,
) -> RuntimeServices:
    """Create chain runtime, pipeline engine, workflow client, and triggers."""

    from ravi.catalog._chain_runtime import ChainRuntime
    from ravi.catalog._pipeline import PipelineEngine, PipelineStore
    from ravi.catalog._triggers.conditions import ConditionMonitor
    from ravi.catalog._triggers.scheduler import TriggerScheduler
    from ravi.catalog._triggers.webhooks import WebhookRegistry
    from ravi.catalog.tools.chain_executor.tool import ChainExecutorTool
    from ravi.catalog.tools.pipeline_manager.tool import PipelineManagerTool

    # ChainRuntime — LLM-written code-based adapter chaining
    chain_runtime = ChainRuntime(catalog=catalog, data_store=data_store)

    # PipelineEngine + PipelineStore — declarative saved adapter chains
    pipeline_engine = PipelineEngine(catalog=catalog, data_store=data_store)
    pipeline_store = PipelineStore(session_factory=session_factory)

    # Restate — durable workflow orchestration (optional, graceful if unavailable)
    restate_ingress = os.environ.get("RESTATE_INGRESS_URL", "http://localhost:8080")
    restate_admin = os.environ.get("RESTATE_ADMIN_URL", "http://localhost:9070")
    workflow_client: Any = None
    try:
        from ravi.adapters.runtime.restate.client import RestateWorkflowClient

        workflow_client = RestateWorkflowClient(
            ingress_url=restate_ingress, admin_url=restate_admin
        )
        await workflow_client.connect()
        logger.info(
            "Restate connected (ingress=%s, admin=%s)", restate_ingress, restate_admin
        )
    except Exception as exc:
        workflow_client = None
        logger.warning("Restate unavailable (%s) — workflow routes disabled", exc)

    # Triggers — autonomous scheduling (cron/interval, webhooks, conditions)
    trigger_scheduler = TriggerScheduler(redis_url=settings.REDIS_URL)
    webhook_registry = WebhookRegistry()
    condition_monitor = ConditionMonitor()

    if workflow_client:
        trigger_scheduler.set_temporal(workflow_client)
        webhook_registry.set_temporal(workflow_client)
        condition_monitor.set_temporal(workflow_client)

    try:
        await trigger_scheduler.start()
    except Exception as exc:
        logger.warning("TriggerScheduler failed to start: %s", exc)

    # Register chain/pipeline tools with their real dependencies
    chain_executor_tool = ChainExecutorTool(chain_runtime=chain_runtime)
    catalog.register_tool(
        chain_executor_tool,
        category="development/execution",
        tags=["chain", "pipe", "automate", "script", "workflow", "adapter"],
        aliases=["chain_tool", "adapter_chain"],
    )
    pipeline_manager_tool = PipelineManagerTool(
        pipeline_engine=pipeline_engine,
        pipeline_store=pipeline_store,
    )
    catalog.register_tool(
        pipeline_manager_tool,
        category="development/execution",
        tags=["pipeline", "save", "run", "workflow", "automation"],
        aliases=["pipeline_tool"],
    )

    # ToolExecutorHandler — registered on runtime for distributed dispatch
    from ravi.catalog.tools._tool_executor import ToolExecutorHandler

    tool_executor_handler = ToolExecutorHandler(
        tools={t.name: t for t in catalog.all_tools()},
        tool_timeout=tool_timeout,
        tools_requiring_approval=tools_requiring_approval,
    )
    await runtime.register("tool_executor", tool_executor_handler)

    return RuntimeServices(
        chain_runtime=chain_runtime,
        pipeline_engine=pipeline_engine,
        pipeline_store=pipeline_store,
        workflow_client=workflow_client,
        trigger_scheduler=trigger_scheduler,
        webhook_registry=webhook_registry,
        condition_monitor=condition_monitor,
    )
