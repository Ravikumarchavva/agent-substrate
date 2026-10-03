"""Serving factory — constructs agents, tools, and runtime for the HTTP shell.

This is the *primary* place substrate_cloud/ and agents/integrations meet — most
routes call these factory functions instead of importing concrete agent or
integration types directly. It is not the ONLY place: several
``substrate_cloud/monolith/routes/*.py`` files (``branches.py``, ``gdpr.py``,
``admin.py``, ``chat.py``, ``knowledge.py``, ``files.py``, ``workspace.py``)
import ``substrate``/``substrate.integrations`` directly too, each
for a narrow, documented reason — see the corresponding
``ignore_imports`` entries in ``pyproject.toml``'s
``"serving cannot import agents or integrations-that-were-capabilities"``
contract.

serving/ is orthogonal to the 3-layer stack (kernel -> agents ->
integrations) so cross-layer imports from substrate and
substrate.integrations are permitted here.
"""

from __future__ import annotations

import logging

import asyncio
import os
import uuid
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any, List, Optional, cast

from sqlalchemy.ext.asyncio import async_sessionmaker

from substrate_cloud.config import SubstrateConfig
from substrate.types import Actor
from substrate.models import ChatModel
from substrate.stores import ThreadStore
from substrate.tools import Tool, ToolRisk, is_hosted_tool, is_provider_defined_tool

logger = logging.getLogger(__name__)


# ── Return containers ─────────────────────────────────────────────────────────


@dataclass
class ChatModels:
    api_keys: dict[str, str]
    model_client_kwargs: dict[str, Any]
    model_client: ChatModel
    chat_model: str


@dataclass
class Infrastructure:
    history: Any
    redis_client: Any
    runtime: Any
    session_factory: async_sessionmaker
    data_store: Any
    bridge_registry: Any
    skill_manager: Any
    file_store: Any
    pending_file_store: Any = None
    artifact_store: Any = None
    workspace_store: Any = None
    short_term_memory: Any = None
    long_term_memory: Any = None
    runtime_stack: AsyncExitStack | None = None
    safety_middleware: Any = None
    store: Any = None  # the one Store behind history, short- and long-term memory; closed once, at shutdown
    task_store: Any = None


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


def init_llm_clients(cfg: SubstrateConfig) -> ChatModels:
    """Create LLM model client and related config."""
    from substrate.integrations.llm.factory import (
        CHAT_MODEL_FALLBACKS,
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
    return ChatModels(
        api_keys=api_keys,
        model_client_kwargs=model_client_kwargs,
        model_client=model_client,
        chat_model=startup_chat_model,
    )


# ── Runtime ───────────────────────────────────────────────────────────────────


def open_store(cfg: SubstrateConfig) -> Any:
    """The one ``Store`` behind history, memory, tasks, vectors, files and the run journal, per ``cfg.STORE_BACKEND``."""
    backend = cfg.STORE_BACKEND.lower()
    if backend == "postgres":
        from substrate.integrations.database import postgres_store

        logger.info(
            "Store: PostgreSQL (schema %s), files under %s",
            cfg.STORE_PG_SCHEMA,
            cfg.STORE_PATH,
        )
        return postgres_store(
            (cfg.ASYNC_DATABASE_URL or cfg.DATABASE_URL).replace("+asyncpg", ""),
            files=cfg.STORE_PATH,
            schema=cfg.STORE_PG_SCHEMA,
            file_quota_bytes=cfg.WORKSPACE_USER_QUOTA_BYTES,
            pool_min_size=cfg.STORE_PG_POOL_MIN_SIZE,
            pool_max_size=cfg.STORE_PG_POOL_MAX_SIZE,
        )
    if backend == "local":
        from substrate.stores import Store

        logger.info("Store: folder %s", cfg.STORE_PATH)
        return Store.at(cfg.STORE_PATH, file_quota_bytes=cfg.WORKSPACE_USER_QUOTA_BYTES)
    raise ValueError(
        f"STORE_BACKEND must be 'postgres' or 'local', got {cfg.STORE_BACKEND!r}"
    )


async def init_runtime(store: Any) -> tuple[Any, AsyncExitStack | None]:
    """The agent runtime, journaling into ``store``. Returns ``(runtime, stack)`` — the caller closes the stack on
    shutdown, which stops the runtime (the store is closed separately, once)."""
    from substrate.runtime import Runtime

    stack = AsyncExitStack()
    runtime = await stack.enter_async_context(Runtime(store))
    return runtime, stack


# ── Infrastructure ────────────────────────────────────────────────────────────


async def _init_file_store(cfg: SubstrateConfig, store: Any) -> Any:
    """The ``FileStore``: S3-compatible object storage when ``FILE_STORE_BACKEND=s3``, else the store's own ``files``."""
    if cfg.FILE_STORE_BACKEND == "s3":
        from substrate.integrations.storage.s3 import S3FileStore

        s3 = S3FileStore(
            endpoint_url=cfg.FILE_STORE_ENDPOINT or "",
            access_key=cfg.FILE_STORE_ACCESS_KEY or "",
            secret_key=cfg.FILE_STORE_SECRET_KEY or "",
            bucket=cfg.FILE_STORE_BUCKET,
            region=cfg.FILE_STORE_REGION,
            user_quota_bytes=cfg.WORKSPACE_USER_QUOTA_BYTES,
        )
        await s3.connect()
        return s3
    return store.files


def _init_pending_file_store(cfg: SubstrateConfig) -> Any:
    """Local-disk store for attachments not yet promoted into the real
    file_store — see integrations/storage/pending.py. Deliberately never
    S3/SeaweedFS-backed, regardless of FILE_STORE_BACKEND: an unsent
    attachment must not touch permanent storage at all."""
    from substrate.integrations.storage.pending import PendingFileStore

    return PendingFileStore(cfg.PENDING_UPLOAD_LOCAL_PATH)


async def init_infrastructure(
    cfg: SubstrateConfig,
    *,
    session_factory: async_sessionmaker,
    model_client: Any = None,
) -> Infrastructure:
    """Create Redis, runtime, file store, and bridge registry.

    ``session_factory`` comes from ``init_db()`` called in lifespan.
    """
    import redis.asyncio as aioredis

    from substrate.integrations.pipeline.data_ref import DataRefStore
    from substrate.integrations.tools.skills._manager import SkillManager
    from substrate_cloud.monolith.sse.bridge import BridgeRegistry

    store = open_store(cfg)
    history = store.threads
    from substrate.workspace import Workspaces

    workspace_store = Workspaces(store)

    short_term_memory = await build_short_term_memory(
        store=store, redis_url=cfg.REDIS_URL, ttl=cfg.REDIS_SESSION_TTL
    )
    # User-scoped standing facts/preferences ("always answer in French") —
    # separate from short_term_memory's per-session scratch state. See
    # build_memory_tool() below for how this gets keyed by user, not thread.
    long_term_memory = store.memory

    # Blocking I/O (HF Hub model download on first run + onnxruntime
    # session construction) — off the event loop via to_thread, same as
    # any other startup-time blocking call in this function would need.
    safety_middleware = await asyncio.to_thread(build_safety_middleware, cfg)

    redis_client = aioredis.from_url(cfg.REDIS_URL, decode_responses=True)

    runtime, runtime_stack = await init_runtime(store)

    task_store: Any = store.tasks

    file_store = await _init_file_store(cfg, store)
    pending_file_store = _init_pending_file_store(cfg)
    # Curated OKF bundles ride on the same object store as files (one
    # bucket, one erasure path, one quota) but under their own key prefix,
    # which the sandbox never mounts — see integrations/artifacts/store.py.
    from substrate.integrations.artifacts import ArtifactStore

    artifact_store = ArtifactStore(file_store)
    if hasattr(file_store, "set_quota_override"):
        # Per-tenant quota overrides (admin storage API) are held in-memory
        # on the store (see Files.set_quota_override) — seed
        # them from their durable copy on every startup. WorkspaceQuota.user_id
        # actually holds a tenant_id (see that model's docstring).
        from sqlalchemy import select

        from substrate_cloud.monolith.models import WorkspaceQuota

        async with session_factory() as session:
            rows = (await session.execute(select(WorkspaceQuota))).scalars().all()
        for row in rows:
            file_store.set_quota_override(row.user_id, row.quota_bytes)
    data_store = DataRefStore(redis_url=cfg.REDIS_URL)
    await data_store.connect()

    bridge_registry = BridgeRegistry(
        response_timeout=300.0,
        store=runtime.store,
    )
    skill_manager = SkillManager(auto_discover=True)

    return Infrastructure(
        history=history,
        redis_client=redis_client,
        runtime=runtime,
        session_factory=session_factory,
        data_store=data_store,
        bridge_registry=bridge_registry,
        skill_manager=skill_manager,
        file_store=file_store,
        pending_file_store=pending_file_store,
        artifact_store=artifact_store,
        workspace_store=workspace_store,
        short_term_memory=short_term_memory,
        long_term_memory=long_term_memory,
        runtime_stack=runtime_stack,
        safety_middleware=safety_middleware,
        store=store,
        task_store=task_store,
    )


# ── Tool registry ─────────────────────────────────────────────────────────────


async def init_tool_registry(
    cfg: SubstrateConfig,
    *,
    session_factory: Any,
    bridge_registry: Any,
    redis_client: Any = None,
    model_client: Any = None,
    file_store: Any = None,
    artifact_store: Any = None,
    workspace_store: Any = None,
    skill_manager: Any = None,
    task_store: Any = None,
    library: Any = None,
    knowledge: Any = None,
) -> ToolboxResult:
    """Create all tools and return a registry.

    ``file_store`` (an ``FileStore``) and ``workspace_store`` (a kernel
    ``WorkspaceStore``) are both needed to stage the code interpreter's
    branch workspace around every run — see ``StagedSandboxRuntime``, which
    now always wraps the sandbox runtime, not just for object-storage
    backends.
    ``skill_manager`` registers the ``skills`` tool (list/activate SKILL.md
    packages under ``integrations/tools/skills/``) — without it the model has
    no way to discover or read a skill's instructions, so a skill existing on
    disk does nothing.
    """
    from substrate.stores import Store
    from substrate.tools import Toolbox
    from substrate.integrations.tools import (
        CalculatorTool,
        CurrentTimeTool,
    )
    from substrate.integrations.tools.code_interpreter import CodeInterpreterTool
    from substrate.integrations.tools.code_interpreter.code_interpreter.runtimes.factory import (
        build_runtime,
        network_policy,
    )
    from substrate.integrations.tools.code_interpreter.code_interpreter.runtimes.staged import (
        StagedSandboxRuntime,
    )
    from substrate.integrations.tools.human_input import AskHumanTool
    from substrate.integrations.tools.task_manager.tool import TaskManagerTool
    from substrate.integrations.tools.web.read_url import ReadUrlTool
    from substrate.integrations.tools.web.search import WebSearchTool

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
        store=task_store or Store.at().tasks, event_sink=_board_event_sink
    )
    ask_tool = AskHumanTool(handler=None, max_requests_per_run=5)  # type: ignore[arg-type]

    code_interpreter_tool: Tool | None = None
    ci_client: Any | None = None

    # Explicit, fail-closed runtime selection: an unusable sandbox raises here
    # (at startup) rather than silently degrading to running untrusted code with
    # no isolation. Without a code interpreter the tool is simply not registered.
    try:
        sandbox_runtime: Any = build_runtime(
            cfg.SANDBOX_RUNTIME,
            workspace_root=cfg.SANDBOX_SCRATCH_ROOT,
            runtime_class_name=cfg.SANDBOX_RUNTIME_CLASS,
            workspace_pvc_claim=cfg.CI_WORKSPACE_PVC_CLAIM or None,
            python_bin=cfg.SANDBOX_PYTHON,
        )
        # Stage for any *local-process* runtime (nsjail, inprocess): the
        # object store holds content-addressed blobs now (agents/workspace/),
        # not a browsable file tree, so even nsjail — same host as the
        # store — needs its branch workspace materialized into scratch
        # before a run and committed back after. No more
        # FILE_STORE_BACKEND-conditional wrapping.
        #
        # k8s is deliberately NOT wrapped here: K8sRuntime.execute() never
        # reads spec.session_dir at all — CodeInterpreterService selects the
        # pod/PVC purely by (tenant_id, user_id) via _ensure_user_template's
        # subPath. Materializing into local scratch on the API server would
        # be invisible to the pod (a different node), so stage-out would
        # commit an empty/stale snapshot every run, silently discarding real
        # history. k8s still isolates per-user (unchanged from before this
        # pass — not a regression), just not yet per-branch; making it
        # branch-aware needs the in-pod server itself to materialize/commit
        # against the workspace store, a separate, untested-here change.
        if (
            cfg.SANDBOX_RUNTIME != "k8s"
            and file_store is not None
            and workspace_store is not None
        ):
            sandbox_runtime = StagedSandboxRuntime(
                sandbox_runtime,
                object_store=file_store,
                workspace_store=workspace_store,
                scratch_root=cfg.SANDBOX_SCRATCH_ROOT,
            )
        elif cfg.SANDBOX_RUNTIME == "k8s":
            logger.warning(
                "Code interpreter on k8s is not yet branch-aware — sandboxed "
                "runs see the whole user's workspace, not just the active "
                "branch. See runtimes/staged.py's module docstring."
            )
        else:
            logger.warning(
                "Code interpreter running WITHOUT workspace staging "
                "(file_store or workspace_store not configured) — branches "
                "will not be isolated from each other."
            )
        code_interpreter_tool = CodeInterpreterTool(
            sandbox_runtime,
            network=network_policy(cfg.SANDBOX_NETWORK_POLICY),
            default_timeout_s=cfg.SANDBOX_TIMEOUT_SECONDS,
            memory_bytes=cfg.SANDBOX_MEMORY_BYTES,
            model_client=model_client,
        )
        logger.info("Code interpreter registered (runtime=%s)", cfg.SANDBOX_RUNTIME)
    except Exception as exc:  # noqa: BLE001 - degrade to "no CI", never to "no isolation"
        code_interpreter_tool = None
        logger.warning(
            "Code interpreter disabled: sandbox runtime %r unavailable (%s)",
            cfg.SANDBOX_RUNTIME,
            exc,
        )

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
    if library is not None:
        from substrate.documents import DocumentsTool
        from substrate_cloud.documents_library import (
            collection_for_scope,
            knowledge_collection_for_scope,
        )

        registry.add(DocumentsTool(library, collection=collection_for_scope))
        if knowledge is not None:
            registry.add(
                DocumentsTool(
                    knowledge,
                    collection=lambda scope: knowledge_collection_for_scope(
                        scope, cfg.KNOWLEDGE_CHAT_BASE
                    ),
                    name="knowledge",
                    description=(
                        "Search and read the organisation's knowledge base: its policies, handbooks and reference documents. "
                        "find(query) is the way in — it searches by meaning and by words; outline and read open what it finds; "
                        "view shows a figure. Cite what you read as [n] with its page."
                    ),
                )
            )
    if artifact_store is not None:
        from substrate.integrations.tools.artifacts import ArtifactsTool

        registry.add(ArtifactsTool(artifact_store, model_name=cfg.CHAT_MODEL))
    if skill_manager is not None:
        from substrate.integrations.tools.skills.tool import SkillTool

        registry.add(SkillTool(skill_manager))

    from substrate.integrations.tools.utils.tool_search import ToolSearchTool

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
    from substrate.runtime.tool_invoker import ToolInvoker
    from substrate.integrations.pipeline.data_ref import DataRefArtifactStore
    from substrate.integrations.pipeline.engine import PipelineEngine
    from substrate.integrations.pipeline.store import PipelineStore
    from substrate.integrations.tools.chain.bridge import ChainBridgeRegistry
    from substrate.integrations.tools.chain.tool import ToolChainTool
    from substrate.integrations.tools.pipeline_manager import PipelineManagerTool
    from substrate.integrations.triggers.conditions import ConditionMonitor
    from substrate.integrations.triggers.scheduler import TriggerScheduler
    from substrate.integrations.triggers.webhooks import WebhookRegistry
    from substrate.integrations.events.redis_event_bus import EventBus
    from substrate.tools import ChainPolicy

    pipeline_engine = PipelineEngine(registry=registry, data_store=data_store)
    pipeline_store = PipelineStore(session_factory=session_factory)

    trigger_scheduler = TriggerScheduler(runtime=runtime)
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
    """Rebuild and register the agent of every active run that carries a recipe.

    A run left over from before a restart can only continue if something can run its
    agent again, so the host rebuilds agents from the recipes stored with their runs.
    A run whose recipe was written by a different version of the code is not special-cased
    here: the worker refuses to replay a run under a version other than the one that
    started it, and fails that run cleanly.
    """
    from substrate.agents.factory import rebuild_agent

    resumable = [run for run in await runtime.store.find_runs() if run.recipe]
    for run in resumable:
        recipe = run.recipe or {}
        tool_names: list[str] = recipe.get("tool_names") or []
        tools = [t for t in registry.all() if getattr(t, "name", None) in tool_names]
        try:
            agent = rebuild_agent(recipe, model_client=model_client, tools=tools)
            agent.id = run.agent  # type: ignore[attr-defined]
            await runtime.register(agent)
            logger.info("Resumed agent %s for run %s", run.agent, run.run_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to resume agent %s: %s", run.agent, exc)
    return len(resumable)


# ── Agent construction ────────────────────────────────────────────────────────


async def build_agent_for_thread(
    thread_id: uuid.UUID,
    *,
    model_client: ChatModel,
    tools: List[Tool],
    system_instructions: str,
    cfg: SubstrateConfig,
    history: Optional[ThreadStore] = None,
    short_term_memory: Any = None,
    long_term_memory: Any = None,
    user_id: str | None = None,
    tenant_id: str | None = None,
    model_context_window: int = 40,
    max_iterations: int = 30,
    runtime: Any = None,
    initial_tool_choice: str | None = None,
    bridge: Any = None,
    safety_middleware: Any = None,
    register: bool = True,
    pinned: bool = True,
    reasoning: Any = None,
) -> Any:
    """Build and register a kernel Agent for this thread.

    ``register``/``pinned`` only affect the final registration step, not
    construction: ``register=False`` returns the built agent without
    registering it at all (for a caller — e.g. a virtual-actor factory —
    that will register the result itself, via ``ActorResolver.resolve()``).
    ``pinned=False`` registers it normally but evictable, the migration path
    for turning this per-thread-per-call agent into a virtual actor without
    touching how it's built — see ``Runtime.register``.

    ``safety_middleware`` (built once at startup by ``build_safety_middleware``,
    threaded through ``app.state``) is appended to the assistant agent's
    TURN-stage middleware — the previously-unused hook this whole guardrail
    system exists to finally wire up. Only applies to the default
    single-assistant path (``cfg.AGENT_MODE != "orchestrator"``); the
    orchestrator's sub-agents are each independent kernel Agents built by
    ``build_research_orchestrator`` and don't currently route through this
    middleware list — a named gap, not an oversight, matching this pass's
    MVP scope (single-assistant is the default and by far the common case).

    Returns a ``ReActAgent`` or ``OrchestratorAgent``.  Serving code
    must treat the return type as ``Any``; the concrete type lives in
    agents/ and must not be imported from substrate_cloud/.

    Agent topology (the fixed researcher/calculator/clock orchestrator, and
    the default single-assistant shape) lives in ``agents/factory.py`` —
    this function only decides which one to build from ``cfg.AGENT_MODE``
    and registers the result(s) with ``runtime``. ``cfg`` is passed in
    rather than imported from a submodule of ``substrate_cloud`` directly —
    this factory stays a leaf callers depend on, not a hub that reaches back
    into the rest of ``substrate_cloud/``.

    ``history`` (the shared ThreadStore) is provided to the agent
    for session conversation history.

    ``bridge`` (the per-thread ``WebHITLBridge``, when given) wires
    ``approval_handler=SSEApprovalHandler(bridge)`` so a CRITICAL/HIGH-risk
    tool call actually pauses for a human decision over SSE instead of
    either running unguarded (no handler configured) or failing closed —
    this is the one real implementation of kernel's ``ApprovalHandler``
    Protocol; see ``substrate_cloud/monolith/sse/approval.py``.
    """
    from substrate.context import build_token_budget_pipeline
    from substrate.agents.factory import create_assistant_agent
    from substrate_cloud.research_orchestrator import build_research_orchestrator

    if runtime is None:
        raise ValueError("build_agent_for_thread() requires a runtime.")

    approval_handler = None
    if bridge is not None:
        from substrate_cloud.monolith.sse.approval import SSEApprovalHandler

        approval_handler = SSEApprovalHandler(bridge)

    session_id = str(thread_id)

    # Standing user facts/preferences, appended as a separately-labeled
    # block — not merged into the base instructions — before the closure
    # below captures system_instructions for cold-store reseeding too, so
    # a reconstructed-from-EventLog turn sees the same block a live one does.
    memory_context = await build_user_memory_context_block(
        long_term_memory, tenant_id, user_id
    )
    if memory_context:
        system_instructions = system_instructions.rstrip() + "\n\n" + memory_context

    if history is None:
        from substrate.stores import Store

        history = Store.at().threads
    # Everything the agent reads or writes of the conversation goes through the tenant's bound handle.
    from substrate.types import Scope
    from substrate.stores import bind_threads

    memory = bind_threads(history, Scope(tenant_id=tenant_id or "default"))

    memory_tool = build_memory_tool(session_id, short_term_memory, long_term_memory)
    if memory_tool is not None:
        tools = [*tools, memory_tool]

    if cfg.AGENT_MODE.lower() == "orchestrator":
        from substrate.integrations.tools import CalculatorTool, CurrentTimeTool
        from substrate.integrations.tools.web.read_url import ReadUrlTool
        from substrate.integrations.tools.web.search import WebSearchTool

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
        approval_handler=approval_handler,
        middleware=[safety_middleware] if safety_middleware is not None else None,
        reasoning=reasoning,
    )
    if register:
        await runtime.register(agent, pinned=pinned)
    return agent


def register_assistant_actor_factory(
    runtime: Any,
    *,
    bridge_registry: Any,
    toolbox: Any,
    model_client: ChatModel,
    system_instructions: str,
    cfg: SubstrateConfig,
    history: Optional[ThreadStore] = None,
    short_term_memory: Any = None,
    long_term_memory: Any = None,
    model_context_window: int = 40,
) -> None:
    """Register the on-demand activation path for chat agents (``type="assistant"``).

    This is the other half of the virtual-actor migration alongside
    ``pinned=False`` above: a factory lets the Worker activate a thread's
    agent purely from its address, for the case nothing has eagerly called
    ``build_agent_for_thread`` yet for it this process (e.g. a message
    delivered to a thread whose actor was evicted, or one that's never been
    built in this process at all). The interactive chat route still calls
    ``build_agent_for_thread`` eagerly for its own turn — this factory is
    the fallback for everything that doesn't go through that route.

    Deliberately reuses ``build_agent_for_thread`` unchanged (``register=
    False`` so this factory — not that function — owns registration, since
    ``ActorResolver.resolve()`` registers the result itself). ``user_id`` is
    not recoverable from the actor's address alone, so an actor activated
    this way gets no personalization block; ``build_agent_for_thread``
    already treats ``user_id`` as optional, so this is a graceful
    degradation, not an error path.
    """

    async def _activate(actor: Actor) -> Any:
        thread_id = uuid.UUID(actor.key)
        bridge = await bridge_registry.acquire(actor.key)
        tools = build_chat_tools(toolbox, bridge)
        return await build_agent_for_thread(
            thread_id,
            model_client=model_client,
            tools=tools,
            system_instructions=system_instructions,
            cfg=cfg,
            history=history,
            short_term_memory=short_term_memory,
            long_term_memory=long_term_memory,
            model_context_window=model_context_window,
            runtime=runtime,
            bridge=bridge,
            register=False,
        )

    runtime.register_factory("assistant", _activate)


def build_chat_tools(toolbox: Any, bridge: Any) -> list[Any]:
    """Return the per-request tool list with AskHumanTool wired to the bridge.

    ``toolbox`` is the shared Toolbox from ``app.state.tools``.
    ``bridge``  is the per-thread WebHITLBridge.

    Serving calls this instead of importing AskHumanTool and WebSurferTool directly.
    """
    from substrate.integrations.tools.human_input import AskHumanTool
    from substrate.integrations.tools.web.search import WebSearchTool
    from substrate.integrations.tools.web.surfer import WebSurferTool

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


async def build_short_term_memory(
    *, store: Any, redis_url: str, ttl: int = 3600
) -> Any:
    """Per-session state: the store's ``session_state``, with a Redis cache in front when ``redis_url`` is set."""
    primary = store.session_state
    if not redis_url:
        return primary

    from substrate.integrations.memory import CachedShortTermMemory, RedisSessionStore

    cache = RedisSessionStore(redis_url=redis_url, ttl=ttl)
    await cache.connect()
    return CachedShortTermMemory(primary=primary, cache=cache)


def build_safety_middleware(cfg: SubstrateConfig) -> Any:
    """Build the multimodal input-safety guardrail (jailbreak/prompt-attack
    text + NSFW image scoring on every live chat turn), or ``None`` if
    disabled.

    Called once, at startup (``init_infrastructure()``) — the classifiers
    load real ONNX model weights (downloading them from the HF Hub on first
    run if not already cached), which must happen eagerly, not lazily on
    the first live request, matching the same "no first-request latency
    cliff" reasoning ``PromptGuardClassifier``/``ImageSafetyClassifier``'s
    own docstrings give for their internal eager session construction.

    Thin pass-through to concrete L1/L2 types, same "legal meeting point"
    convention as ``build_short_term_memory``
    above — this module is the one place substrate_cloud/'s dependency chain is
    allowed to construct agents/capabilities concrete types.
    """
    enabled = getattr(cfg, "ENABLE_TEXT_SAFETY_GUARD", True)
    if not enabled:
        logger.info(
            "MultimodalSafetyMiddleware disabled (ENABLE_TEXT_SAFETY_GUARD=false)"
        )
        return None

    from substrate.middleware import MultimodalSafetyMiddleware
    from substrate.integrations.safety.image_classifier import ImageSafetyClassifier
    from substrate.integrations.safety.text_classifier import PromptGuardClassifier

    text_threshold = getattr(cfg, "SAFETY_TEXT_THRESHOLD", 0.9)
    nsfw_threshold = getattr(cfg, "SAFETY_IMAGE_NSFW_THRESHOLD", 0.5)
    nsfl_threshold = getattr(cfg, "SAFETY_IMAGE_NSFL_THRESHOLD", 0.3)

    try:
        text_classifier = PromptGuardClassifier(threshold=text_threshold)
        image_classifier = ImageSafetyClassifier(
            nsfw_threshold=nsfw_threshold, nsfl_threshold=nsfl_threshold
        )
    except Exception:
        # Fail-open on infrastructure failure (model didn't load, no network
        # to the Hub on first run, etc.) — a bad deploy must not take down
        # all chat. A missing guardrail is logged loudly; a down monolith
        # is not an acceptable tradeoff for it. See the plan's production-
        # hardening notes for the fail-open/fail-closed split (this is the
        # infrastructure-failure half; an actual malicious verdict still
        # fails closed inside the middleware itself).
        logger.exception(
            "MultimodalSafetyMiddleware failed to initialize — chat will run "
            "WITHOUT the safety guardrail. Fix and restart to re-enable."
        )
        return None

    return MultimodalSafetyMiddleware(
        text_classifier=text_classifier, image_classifier=image_classifier
    )


async def build_cached_history_for_thread(
    thread_id: str,
    *,
    system_instructions: str,
    history: Any,
    conversation_service_url: str,
) -> Any:
    """Return the history provider for this thread."""
    if history is not None:
        return history
    from substrate.stores import Store

    return Store.at().threads


def build_memory_tool(
    session_id: str,
    short_term_memory: Any,
    long_term_memory: Any = None,
) -> Any | None:
    """Build a ``MemoryTool`` bound to *session_id*, or ``None`` if neither
    memory backend is configured.

    Short-term ops (get/set/clear_session) are scoped to *session_id*. Long-term ops
    (remember/recall/forget) are scoped to the person the run is for — tenant and user are
    read from the run's scope when a call is made, so a fact saved in one thread is visible
    in every other thread that same user opens later. A run with no authenticated user keeps
    its facts for that conversation only.
    """
    if short_term_memory is None and long_term_memory is None:
        return None
    from substrate.integrations.tools.memory import MemoryTool

    return MemoryTool(
        session_id, short_term=short_term_memory, long_term=long_term_memory
    )


def _xml_escape(text: str) -> str:
    """Minimal XML escaping — same helper shape as
    ``integrations/tools/skills/_manager.py``'s (not imported: this module
    lives orthogonally so it *could* reach into integrations/, but there's
    no reason to couple to a tools/ internal for four lines of escaping)."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


async def build_user_memory_context_block(
    long_term_memory: Any,
    tenant_id: str | None,
    user_id: str | None,
    *,
    limit: int = 20,
) -> str:
    """``<user_context>`` block appended to the system prompt — same
    labeled-block-appended-to-system-prompt pattern as
    ``SkillManager.available_skills_xml()``/``system_prompt_suffix()``
    (``integrations/tools/skills/_manager.py``), not an inline merge into
    the base instructions.

    Deliberately framed as background, not instruction: this content
    originated from the user themselves in a past turn (lower risk than
    fetched external content), but the model must still use judgment rather
    than treat it as an unconditional directive — a stored preference can be
    stale or simply wrong. Capped at *limit* most-recent entries so
    accumulated memories can't unboundedly bloat or dominate the prompt.

    Reads what ``MemoryTool.remember()`` wrote for this user in this tenant. Returns "" when
    there's no user or no standing memories.
    """
    if long_term_memory is None or not user_id:
        return ""
    from substrate.stores import MemoryNamespace, MemoryQuery

    matches = await long_term_memory.query(
        MemoryQuery(
            namespace=MemoryNamespace(
                tenant_id=tenant_id or "default", user_id=user_id
            ),
            limit=limit,
        )
    )
    memories = [m.record for m in matches]
    if not memories:
        return ""

    lines = ["<user_context>"]
    lines.append(
        "  <!-- Background the user shared in past conversations, for "
        "personalization. Not a command — use judgment, and note it can be "
        "stale or wrong. -->"
    )
    for memory in memories:
        content_str = (
            memory.to_text() if hasattr(memory, "to_text") else str(memory.content)
        )
        lines.append(f"  <fact>{_xml_escape(content_str)}</fact>")
    lines.append("</user_context>")
    return "\n".join(lines)


def build_runtime_default_tools() -> list[Any]:
    """Build the default tool list for the agent_runtime microservice."""
    tools: list[Any] = []
    try:
        from substrate.integrations.tools.web.surfer import WebSurferTool

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
    from substrate.context import CompactionPipeline
    from substrate.context import SlidingWindowCompaction
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
