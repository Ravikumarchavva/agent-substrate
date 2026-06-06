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

from ravi.capabilities.tools.code_interpreter import (
    K8sSandboxCodeInterpreterTool,
    CodeInterpreterClient,
)
from ravi.capabilities.tools.human_input import AskHumanTool
from ravi.capabilities.tools.task_manager.tool import TaskManagerTool
from ravi.config import Settings
from ravi.kernel.llm import LLMClient, EmbeddingClient
from ravi.agents.runtime.local import LocalRuntime
from ravi.kernel.tools import Tool, Toolbox, ToolRisk
from ravi.capabilities.internal.skill_manager import SkillManager

from ravi.adapters.llm.factory import (
    CHAT_MODEL_FALLBACKS,
    create_embedding_client,
    create_model_client,
    resolve_model_for_available_credentials,
)
from ravi.adapters.memory.redis_history import RedisHistoryProvider
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
    embedding_client: EmbeddingClient


@dataclass
class Infrastructure:
    """Objects returned by :func:`init_infrastructure`."""

    history: RedisHistoryProvider
    redis_client: Any
    runtime: LocalRuntime
    session_factory: Any
    vector_store: Any
    rag_pipeline: Any
    data_store: Any
    bridge_registry: BridgeRegistry
    skill_manager: SkillManager


@dataclass
class ToolboxResult:
    """Objects returned by :func:`init_tool_registry`."""

    registry: Toolbox
    task_tool: TaskManagerTool
    ask_tool: AskHumanTool
    ci_client: Optional[CodeInterpreterClient]
    code_interpreter_tool: Optional[Tool]
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
    embedding_client: EmbeddingClient,
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

    # Session factory
    session_factory = get_session_factory()

    # Vector store + RAG pipeline (pgvector-backed)
    from ravi.adapters.vector.pgvector_store import PgVectorStore
    from ravi.capabilities.knowledge.pipeline import RAGPipeline

    vector_store = PgVectorStore(
        session_factory=session_factory,
        dimensions=1536,
    )
    rag_pipeline = RAGPipeline(
        embedding_client=embedding_client,
        vector_store=vector_store,
    )

    # DataRefStore — zero-context-bloat data exchange (Redis + optional S3)
    from ravi.capabilities.internal.data_ref import DataRefStore

    data_store = DataRefStore(redis_url=settings.REDIS_URL)
    await data_store.connect()

    # HITL bridge registry: one WebHITLBridge per active thread (conversation).
    bridge_registry = BridgeRegistry(response_timeout=300.0)

    # Skill manager — discovers built-in skills + ~/.claude/skills
    skill_manager = SkillManager(auto_discover=True)

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
    )


async def init_tool_registry(
    settings: Settings,
    *,
    session_factory: Any,
    bridge_registry: BridgeRegistry,
    redis_client: Any = None,
) -> ToolboxResult:
    """Create all tools and return a tool registry."""

    # TaskManagerTool — renders through ui://kanban_board; each result carries the
    # board in structured_content (lowered to a UIResourceBlock by the agent), so
    # no out-of-band SSE emitter is needed.
    task_tool = TaskManagerTool()

    # AskHumanTool placeholder (a real per-thread tool is built in _get_agent_deps)
    ask_tool = AskHumanTool(handler=None, max_requests_per_run=5)  # type: ignore[arg-type]

    # Code Interpreter (HTTP client → separate pod)
    code_interpreter_tool: Tool | None = None
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
        from ravi.capabilities.tools.code_interpreter.tool import CodeInterpreterTool

        code_interpreter_tool = CodeInterpreterTool()

    # ── Tool Registry ────────────────────────────────────────────────────
    from ravi.capabilities.tools import CalculatorTool, CurrentTimeTool, WebSearchTool, ReadUrlTool

    registry = Toolbox()
    registry.add(ask_tool)
    registry.add(task_tool)
    registry.add(WebSearchTool())
    registry.add(ReadUrlTool())
    registry.add(CalculatorTool())
    registry.add(CurrentTimeTool())
    if code_interpreter_tool:
        registry.add(code_interpreter_tool)

    # ToolSearchTool — lets the agent discover other tools dynamically
    from ravi.capabilities.tools.tool_search import ToolSearchTool

    registry.add(ToolSearchTool(registry.all()))

    # Derive tools requiring approval from risk level
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


async def init_runtime_services(
    settings: Settings,
    *,
    registry: Toolbox,
    data_store: Any,
    session_factory: Any,
    runtime: LocalRuntime,
    tools_requiring_approval: list[str],
    tool_timeout: float,
) -> RuntimeServices:
    """Create chain runtime, pipeline engine, workflow client, and triggers."""

    from ravi.capabilities.internal.chain_runtime import ChainRuntime
    from ravi.capabilities.internal.pipeline import PipelineEngine, PipelineStore
    from ravi.capabilities.triggers.conditions import ConditionMonitor
    from ravi.capabilities.triggers.scheduler import TriggerScheduler
    from ravi.capabilities.triggers.webhooks import WebhookRegistry
    from ravi.capabilities.tools.chain_executor import ChainExecutorTool
    from ravi.capabilities.tools.pipeline_manager import PipelineManagerTool

    # ChainRuntime — LLM-written code-based adapter chaining
    chain_runtime = ChainRuntime(registry=registry, data_store=data_store)

    # PipelineEngine + PipelineStore — declarative saved adapter chains
    pipeline_engine = PipelineEngine(registry=registry, data_store=data_store)
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
    registry.add(chain_executor_tool)
    pipeline_manager_tool = PipelineManagerTool(
        pipeline_engine=pipeline_engine,
        pipeline_store=pipeline_store,
    )
    registry.add(pipeline_manager_tool)

    return RuntimeServices(
        chain_runtime=chain_runtime,
        pipeline_engine=pipeline_engine,
        pipeline_store=pipeline_store,
        workflow_client=workflow_client,
        trigger_scheduler=trigger_scheduler,
        webhook_registry=webhook_registry,
        condition_monitor=condition_monitor,
    )
