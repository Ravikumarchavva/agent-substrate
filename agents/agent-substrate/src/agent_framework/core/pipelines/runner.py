"""PipelineRunner — build live framework objects from a PipelineConfig.

Takes the JSON graph produced by the visual builder and instantiates
real ``ReActAgent``, ``BaseTool``, ``LLMJudge``, ``StructuredRouter``,
``SkillManager``, and ``BaseMemory`` objects wired together exactly as
the user drew them on the canvas.

Usage::

    from agent_framework.core.pipelines import PipelineRunner, PipelineConfig

    config = PipelineConfig.model_validate(json_from_db)
    pipeline = PipelineRunner()

    # ``app_state`` is the FastAPI app.state that holds tool instances, etc.
    runnable = await pipeline.build(config, tools_registry=app_state.tools,
                                     model_client=app_state.model_client,
                                     redis_memory=app_state.redis_memory)

    # ``runnable`` is either a ReActAgent (single agent) or StructuredRouter
    if hasattr(runnable, 'run'):
        result = await runnable.run("Hello!")
    else:
        decision, result = await runnable.route(messages)
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Union

from agent_framework.core.agents.react_agent import ReActAgent
from agent_framework.core.context.base_context import ModelContext
from agent_framework.core.guardrails.base_guardrail import BaseGuardrail
from agent_framework.core.memory.base_memory import BaseMemory
from agent_framework.core.memory.redis_memory import RedisMemory
from agent_framework.core.memory.unbounded_memory import UnboundedMemory
from agent_framework.core.pipelines.schema import (
    EdgeType,
    NodeConfig,
    NodeType,
    PipelineConfig,
)
from agent_framework.core.pipelines.condition_runner import ConditionPipelineRunner
from agent_framework.core.pipelines.while_runner import WhilePipelineRunner
from agent_framework.core.structured.judge import LLMJudge
from agent_framework.core.structured.router import StructuredRouter
from agent_framework.core.tools.base_tool import BaseTool
from agent_framework.extensions.skills import SkillManager
from agent_framework.providers.llm.base_client import BaseModelClient
from agent_framework.extensions.mcp.client import MCPClient
from agent_framework.extensions.mcp.tool import MCPTool

logger = logging.getLogger("agent_framework.pipelines.runner")

# Schema name → Pydantic class mapping for built-in guardrail schemas
_GUARDRAIL_SCHEMAS: Dict[str, Any] = {}

def _load_guardrail_schemas() -> Dict[str, Any]:
    """Lazy-load to avoid circular imports."""
    global _GUARDRAIL_SCHEMAS
    if not _GUARDRAIL_SCHEMAS:
        from agent_framework.core.structured.schemas import (
            ContentSafetyJudge,
            RelevanceJudge,
        )
        _GUARDRAIL_SCHEMAS = {
            "ContentSafetyJudge": ContentSafetyJudge,
            "RelevanceJudge": RelevanceJudge,
        }
    return _GUARDRAIL_SCHEMAS


class PipelineRunner:
    """Turns a visual-builder ``PipelineConfig`` into live objects."""

    async def build(
        self,
        config: PipelineConfig,
        *,
        tools_registry: List[BaseTool],
        model_client: BaseModelClient,
        redis_memory: Optional[RedisMemory] = None,
        model_context: Optional[ModelContext] = None,
        session_id: Optional[str] = None,
        hitl_bridge: Optional[Any] = None,
    ) -> Union[ReActAgent, StructuredRouter, "ConditionPipelineRunner", "WhilePipelineRunner"]:
        """Build the pipeline graph into a runnable agent or router.

        Topology detection (in priority order):
        1. ``while`` node     → ``WhilePipelineRunner`` (repeat-until loop)
        2. ``condition`` node  → ``ConditionPipelineRunner`` (expression-based branching)
        3. ``router`` node     → ``StructuredRouter`` (LLM-based routing)
        4. First ``agent``     → single ``ReActAgent``

        ``start``, ``end``, and ``note`` nodes are structural-only and are
        always skipped at runtime.
        """
        # While-loop pipeline
        while_nodes = config.nodes_by_type(NodeType.WHILE)
        if while_nodes:
            return await self._build_while_pipeline(
                config, while_nodes[0],
                tools_registry=tools_registry,
                model_client=model_client,
                redis_memory=redis_memory,
                model_context=model_context,
                session_id=session_id,
                hitl_bridge=hitl_bridge,
            )

        # Condition-based branching pipeline
        condition_nodes = config.nodes_by_type(NodeType.CONDITION)
        if condition_nodes:
            return await self._build_condition_pipeline(
                config, condition_nodes[0],
                tools_registry=tools_registry,
                model_client=model_client,
                redis_memory=redis_memory,
                model_context=model_context,
                session_id=session_id,
                hitl_bridge=hitl_bridge,
            )

        # LLM-based routing
        router_nodes = config.nodes_by_type(NodeType.ROUTER)
        if router_nodes:
            return await self._build_router(
                config, router_nodes[0],
                tools_registry=tools_registry,
                model_client=model_client,
                redis_memory=redis_memory,
                model_context=model_context,
                session_id=session_id,
            )

        # Single agent (possibly with approval HITL)
        agent_nodes = config.nodes_by_type(NodeType.AGENT)
        if not agent_nodes:
            raise ValueError("Pipeline has no agent, router, or condition node")

        agent = await self._build_agent(
            config, agent_nodes[0],
            tools_registry=tools_registry,
            model_client=model_client,
            redis_memory=redis_memory,
            model_context=model_context,
            session_id=session_id,
            hitl_bridge=hitl_bridge,
        )
        return agent

    # ── Agent builder ────────────────────────────────────────────────────

    # ── While-loop pipeline builder ──────────────────────────────────────

    async def _build_while_pipeline(
        self,
        config: PipelineConfig,
        while_node: NodeConfig,
        *,
        tools_registry: List[BaseTool],
        model_client: BaseModelClient,
        redis_memory: Optional[RedisMemory] = None,
        model_context: Optional[ModelContext] = None,
        session_id: Optional[str] = None,
        hitl_bridge: Optional[Any] = None,
    ) -> "WhilePipelineRunner":
        """Build a WhilePipelineRunner from a while node.

        Expects:
        - One agent connected via a ``while_body`` edge (the loop body).
        - Optionally one agent connected via a ``while_done`` edge (post-loop).
        """
        body_agent_node: Optional[NodeConfig] = None
        done_agent_node: Optional[NodeConfig] = None

        for edge in config.edges_from(while_node.id):
            target = config.node_by_id(edge.target)
            if not target or target.node_type != NodeType.AGENT:
                continue
            handle = edge.source_handle or ""
            if handle == "done":
                done_agent_node = target
            else:
                body_agent_node = target  # body / default

        # Fall back: pick the first agent in graph as the body
        if body_agent_node is None:
            agent_nodes = config.nodes_by_type(NodeType.AGENT)
            body_agent_node = agent_nodes[0] if agent_nodes else None

        if body_agent_node is None:
            raise ValueError("While loop has no body agent connected")

        body_agent = await self._build_agent(
            config, body_agent_node,
            tools_registry=tools_registry,
            model_client=model_client,
            redis_memory=redis_memory,
            model_context=model_context,
            session_id=session_id,
            hitl_bridge=hitl_bridge,
        )

        done_agent = None
        if done_agent_node:
            done_agent = await self._build_agent(
                config, done_agent_node,
                tools_registry=tools_registry,
                model_client=model_client,
                redis_memory=redis_memory,
                model_context=model_context,
                session_id=session_id,
                hitl_bridge=hitl_bridge,
            )

        return WhilePipelineRunner(
            body_agent=body_agent,
            condition=str(while_node.config.get("condition", "")),
            max_iterations=int(while_node.config.get("max_iterations", 10)),
            done_agent=done_agent,
        )

    # ── Condition pipeline builder ───────────────────────────────────────

    async def _build_condition_pipeline(
        self,
        config: PipelineConfig,
        condition_node: NodeConfig,
        *,
        tools_registry: List[BaseTool],
        model_client: BaseModelClient,
        redis_memory: Optional[RedisMemory] = None,
        model_context: Optional[ModelContext] = None,
        session_id: Optional[str] = None,
        hitl_bridge: Optional[Any] = None,
    ) -> "ConditionPipelineRunner":
        """Build a ConditionPipelineRunner from a condition node."""
        # Find the upstream agent (connected to the condition node)
        upstream_agents = [
            config.node_by_id(e.source)
            for e in config.edges_to(condition_node.id)
            if config.node_by_id(e.source) and config.node_by_id(e.source).node_type == NodeType.AGENT
        ]
        if not upstream_agents:
            # Fall back to the first agent in the graph
            upstream_agents = config.nodes_by_type(NodeType.AGENT)[:1]

        upstream_agent = await self._build_agent(
            config, upstream_agents[0],
            tools_registry=tools_registry,
            model_client=model_client,
            redis_memory=redis_memory,
            model_context=model_context,
            session_id=session_id,
            hitl_bridge=hitl_bridge,
        ) if upstream_agents else None

        # Build branch agents (condition branches and else)
        branch_agents: Dict[str, ReActAgent] = {}
        else_agent: Optional[ReActAgent] = None

        for edge in config.edges_from(condition_node.id):
            target_node = config.node_by_id(edge.target)
            if not target_node or target_node.node_type != NodeType.AGENT:
                continue
            branch_agent = await self._build_agent(
                config, target_node,
                tools_registry=tools_registry,
                model_client=model_client,
                redis_memory=redis_memory,
                model_context=model_context,
                session_id=session_id,
                hitl_bridge=hitl_bridge,
            )
            handle = edge.source_handle or edge.label
            if handle == "else":
                else_agent = branch_agent
            else:
                branch_agents[handle] = branch_agent

        conditions = condition_node.config.get("conditions", [])
        return ConditionPipelineRunner(
            upstream_agent=upstream_agent,
            conditions=conditions,
            branch_agents=branch_agents,
            else_agent=else_agent,
        )

    async def _build_agent(
        self,
        config: PipelineConfig,
        agent_node: NodeConfig,
        *,
        tools_registry: List[BaseTool],
        model_client: BaseModelClient,
        redis_memory: Optional[RedisMemory] = None,
        model_context: Optional[ModelContext] = None,
        session_id: Optional[str] = None,
        hitl_bridge: Optional[Any] = None,
    ) -> ReActAgent:
        """Build a single ReActAgent from an agent node and its edges."""
        cfg = agent_node.config

        # -- Tools: find connected tool nodes --
        tool_edges = [
            e for e in config.edges_from(agent_node.id)
            if e.edge_type == EdgeType.AGENT_TOOL
        ]
        tools = self._resolve_tools(config, tool_edges, tools_registry)

        # -- MCP server nodes: connect and inject their tools --
        mcp_edges = [
            e for e in config.edges_to(agent_node.id)
            if e.edge_type == EdgeType.AGENT_MCP
        ]
        mcp_tools = await self._resolve_mcp_tools(config, mcp_edges)
        tools = [*tools, *mcp_tools]

        # -- Approval nodes: inject AskHumanTool if connected approval gate --
        approval_nodes = [
            config.node_by_id(e.target)
            for e in config.edges_from(agent_node.id)
            if config.node_by_id(e.target)
            and config.node_by_id(e.target).node_type == NodeType.APPROVAL
        ]
        if approval_nodes and hitl_bridge is not None:
            try:
                from agent_framework.extensions.tools.human_input import AskHumanTool
                tools = [*tools, AskHumanTool(bridge=hitl_bridge)]
                logger.info("Injected AskHumanTool for approval node %s", approval_nodes[0].id)
            except Exception as exc:
                logger.warning("Could not inject AskHumanTool: %s", exc)

        # -- Guardrails --
        guardrail_edges = [
            e for e in config.edges_from(agent_node.id)
            if e.edge_type == EdgeType.AGENT_GUARDRAIL
        ]
        input_guardrails, output_guardrails = self._resolve_guardrails(
            config, guardrail_edges, model_client
        )

        # -- Memory --
        memory = await self._resolve_memory(
            config, agent_node, redis_memory=redis_memory, session_id=session_id
        )

        # -- Skills --
        skill_manager = self._resolve_skills(config, agent_node)

        # -- Model context --
        if model_context is None:
            from agent_framework.core.context.implementations import SlidingWindowContext
            window = cfg.get("context_window", 40)
            model_context = SlidingWindowContext(max_messages=window)

        # -- Model client (could be overridden per agent node) --
        agent_model = cfg.get("model", "gpt-4o-mini")
        if agent_model != getattr(model_client, "model", None):
            # Build a new client with the different model, preserving the API key.
            # Try multiple attribute paths: self.api_key → self.client.api_key → env var
            from agent_framework.providers.llm.openai.openai_client import OpenAIClient
            api_key = (
                getattr(model_client, "api_key", None)
                or getattr(getattr(model_client, "client", None), "api_key", None)
                or ""
            )
            agent_client = OpenAIClient(model=agent_model, api_key=api_key or None)
        else:
            agent_client = model_client

        agent = ReActAgent(
            name=agent_node.label or cfg.get("name", "pipeline-agent"),
            description=cfg.get("description", "Pipeline-built agent"),
            model_client=agent_client,
            model_context=model_context,
            tools=tools,
            system_instructions=cfg.get("system_prompt", "You are a helpful assistant."),
            memory=memory,
            max_iterations=cfg.get("max_iterations", 10),
            input_guardrails=input_guardrails or None,
            output_guardrails=output_guardrails or None,
            skill_manager=skill_manager,
        )
        return agent

    # ── Router builder ───────────────────────────────────────────────────

    async def _build_router(
        self,
        config: PipelineConfig,
        router_node: NodeConfig,
        *,
        tools_registry: List[BaseTool],
        model_client: BaseModelClient,
        redis_memory: Optional[RedisMemory] = None,
        model_context: Optional[ModelContext] = None,
        session_id: Optional[str] = None,
        hitl_bridge: Optional[Any] = None,
    ) -> StructuredRouter:
        """Build a StructuredRouter from a router node and its route edges."""
        cfg = router_node.config

        # Build a dynamic Pydantic model from the config's routing_fields
        routing_schema = self._build_routing_schema(cfg)
        routing_key = cfg.get("routing_key", "category")

        # Find route edges → build sub-agents for each
        route_edges = [
            e for e in config.edges_from(router_node.id)
            if e.edge_type == EdgeType.ROUTER_ROUTE
        ]

        routes: Dict[str, Any] = {}
        for edge in route_edges:
            target_node = config.node_by_id(edge.target)
            if target_node and target_node.node_type == NodeType.AGENT:
                sub_agent = await self._build_agent(
                    config, target_node,
                    tools_registry=tools_registry,
                    model_client=model_client,
                    redis_memory=redis_memory,
                    model_context=model_context,
                    session_id=session_id,
                    hitl_bridge=hitl_bridge,
                )
                routes[edge.label] = sub_agent

        router = StructuredRouter(
            client=model_client,
            routing_schema=routing_schema,
            routing_key=routing_key,
            routes=routes,
            system_prompt=cfg.get("system_prompt", "Route the request."),
        )
        return router

    # ── Helpers ──────────────────────────────────────────────────────────

    def _resolve_tools(
        self,
        config: PipelineConfig,
        tool_edges: list,
        tools_registry: List[BaseTool],
    ) -> List[BaseTool]:
        """Lookup tool instances from the global registry by name."""
        tool_map = {t.name: t for t in tools_registry}
        tools: List[BaseTool] = []
        for edge in tool_edges:
            tool_node = config.node_by_id(edge.target)
            if not tool_node:
                continue
            tool_name = tool_node.config.get("tool_name", tool_node.label)
            tool = tool_map.get(tool_name)
            if tool:
                tools.append(tool)
            else:
                logger.warning("Tool %r not found in registry — skipped", tool_name)
        return tools

    async def _resolve_mcp_tools(
        self,
        config: PipelineConfig,
        mcp_edges: list,
    ) -> List[BaseTool]:
        """Connect to MCP server nodes and collect their tools as MCPTool instances.

        Each MCP node config must have:
          - ``url``: SSE endpoint URL (required for ``transport: sse``)
          - ``command`` + optionally ``args``: for ``transport: stdio``
          - ``server_name``: human-readable label
          - ``transport``: ``"sse"`` (default) or ``"stdio"``
          - ``enabled_tools``: list of tool names to expose (empty = all)
        """
        result: List[BaseTool] = []
        for edge in mcp_edges:
            mcp_node = config.node_by_id(edge.source)
            if not mcp_node or mcp_node.node_type != NodeType.MCP:
                continue
            cfg = mcp_node.config
            server_name = cfg.get("server_name", "mcp-server")
            transport = cfg.get("transport", "sse")
            client = MCPClient()
            try:
                if transport == "stdio":
                    command = cfg.get("command", "")
                    args: List[str] = cfg.get("args") or []
                    if not command:
                        logger.warning("MCP node %r has no command — skipped", mcp_node.id)
                        continue
                    await client.connect_stdio(command, args)
                else:
                    url = cfg.get("url", "")
                    if not url:
                        logger.warning("MCP node %r has no url — skipped", mcp_node.id)
                        continue
                    await client.connect_sse(url)

                raw_tools = await client.list_tools()
                enabled: set[str] = set(cfg.get("enabled_tools") or [])
                for raw in raw_tools:
                    name = raw.get("name", "")
                    if enabled and name not in enabled:
                        continue
                    result.append(
                        MCPTool(
                            client=client,
                            name=name,
                            description=raw.get("description", ""),
                            input_schema=raw.get("inputSchema", {}),
                        )
                    )
                logger.info(
                    "MCP server %r connected (%s tools)", server_name, len(result)
                )
            except Exception as exc:
                logger.warning(
                    "Failed to connect MCP server %r (%s): %s", server_name, transport, exc
                )
        return result

    def _resolve_guardrails(
        self,
        config: PipelineConfig,
        guardrail_edges: list,
        model_client: BaseModelClient,
    ) -> tuple[List[BaseGuardrail], List[BaseGuardrail]]:
        """Build guardrail instances from guardrail nodes."""
        schemas = _load_guardrail_schemas()
        input_guards: List[BaseGuardrail] = []
        output_guards: List[BaseGuardrail] = []

        for edge in guardrail_edges:
            guard_node = config.node_by_id(edge.target)
            if not guard_node:
                continue
            gcfg = guard_node.config
            # Frontend sends "schema_name"; fallback to legacy "schema" key
            schema_name = gcfg.get("schema_name") or gcfg.get("schema", "ContentSafetyJudge")
            schema_cls = schemas.get(schema_name)
            if not schema_cls:
                logger.warning("Unknown guardrail schema %r — skipped", schema_name)
                continue

            judge = LLMJudge(
                client=model_client,
                schema=schema_cls,
                system_prompt=gcfg.get("system_prompt", f"Evaluate using {schema_name}."),
                pass_field=gcfg.get("pass_field", "safe"),
                tripwire_on_fail=gcfg.get("tripwire_on_fail", False),
                name=guard_node.label or f"judge_{schema_name}",
            )

            guard_type = gcfg.get("guardrail_type", "output")
            if guard_type == "input":
                input_guards.append(judge)
            else:
                output_guards.append(judge)

        return input_guards, output_guards

    async def _resolve_memory(
        self,
        config: PipelineConfig,
        agent_node: NodeConfig,
        *,
        redis_memory: Optional[RedisMemory] = None,
        session_id: Optional[str] = None,
    ) -> BaseMemory:
        """Build the memory backend from the connected memory node."""
        mem_edges = [
            e for e in config.edges_from(agent_node.id)
            if e.edge_type == EdgeType.AGENT_MEMORY
        ]
        if not mem_edges:
            return UnboundedMemory()

        mem_node = config.node_by_id(mem_edges[0].target)
        if not mem_node:
            return UnboundedMemory()

        mcfg = mem_node.config
        backend = mcfg.get("backend", "unbounded")

        if backend == "redis" and redis_memory is not None:
            sid = session_id or f"pipeline-{agent_node.id}"
            mem = RedisMemory(
                session_id=sid,
                redis_url=redis_memory._redis_url if hasattr(redis_memory, "_redis_url") else "redis://localhost:6379/0",
                default_ttl=mcfg.get("ttl", 3600),
                max_messages=mcfg.get("max_messages", 200),
            )
            await mem.connect()
            return mem

        return UnboundedMemory()

    def _resolve_skills(
        self,
        config: PipelineConfig,
        agent_node: NodeConfig,
    ) -> Optional[SkillManager]:
        """Build a SkillManager if skills are connected."""
        skill_edges = [
            e for e in config.edges_from(agent_node.id)
            if e.edge_type == EdgeType.AGENT_SKILL
        ]
        if not skill_edges:
            return None

        # Skill nodes just carry a skill_name — the SkillManager discovers from dirs
        skill_names = []
        for edge in skill_edges:
            skill_node = config.node_by_id(edge.target)
            if skill_node:
                skill_names.append(
                    skill_node.config.get("skill_name", skill_node.label)
                )

        if not skill_names:
            return None

        # Use default skill directories — the manager will discover all
        # and we'll activate only the connected ones
        manager = SkillManager(auto_discover=True)
        for name in skill_names:
            try:
                manager.activate(name)
            except Exception as exc:
                logger.warning("Could not activate skill %r: %s", name, exc)
        return manager

    @staticmethod
    def _build_routing_schema(cfg: Dict[str, Any]) -> type:
        """Dynamically build a Pydantic model from routing_fields config."""
        from pydantic import create_model, Field as PydanticField

        fields_def = cfg.get("routing_fields", [])
        if not fields_def:
            # Fallback: simple category + reasoning
            fields_def = [
                {"name": "category", "type": "str", "description": "The routing category"},
                {"name": "reasoning", "type": "str", "description": "Explanation"},
            ]

        type_map = {"str": str, "int": int, "float": float, "bool": bool}
        field_definitions: Dict[str, Any] = {}
        for f in fields_def:
            py_type = type_map.get(f.get("type", "str"), str)
            desc = f.get("description", "")
            field_definitions[f["name"]] = (
                py_type,
                PydanticField(description=desc),
            )

        return create_model("DynamicRoutingSchema", **field_definitions)
