"""Pydantic v2 schemas for visual-builder pipeline graphs.

A pipeline is a directed graph of **nodes** (agents, tools, skills,
guardrails, routers, memory backends) connected by typed **edges**.

These schemas are:
  1. Serialised to/from JSONB in the ``Pipeline`` DB model.
  2. Sent over the wire in the ``/builder`` REST API.
  3. Consumed by ``PipelineRunner.build()`` to construct live agent objects.
  4. Consumed by ``generate_code()`` to emit standalone Python modules.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class NodeType(str, Enum):
    """Allowed node types in a pipeline graph."""

    AGENT = "agent"
    TOOL = "tool"
    SKILL = "skill"
    GUARDRAIL = "guardrail"
    ROUTER = "router"
    MEMORY = "memory"
    # Flow-control / structural nodes
    START = "start"
    END = "end"
    NOTE = "note"  # documentation only — no runtime effect
    CONDITION = "condition"  # expression-based branching
    APPROVAL = "approval"  # human-in-the-loop gate
    WHILE = "while"  # repeat-until loop
    MCP = "mcp"  # external MCP server (SSE or stdio)


class EdgeType(str, Enum):
    """Semantic edge types — drives validation and rendering."""

    AGENT_TOOL = "agent_tool"  # agent → tool
    AGENT_GUARDRAIL = "agent_guardrail"  # agent → guardrail
    AGENT_SKILL = "agent_skill"  # agent → skill
    AGENT_MEMORY = "agent_memory"  # agent → memory
    ROUTER_ROUTE = "router_route"  # router → agent (one per route label)
    ROUTER_INPUT = "router_input"  # upstream → router (incoming edge)
    # Condition branching
    CONDITION_INPUT = "condition_input"  # upstream → condition node
    CONDITION_BRANCH = "condition_branch"  # condition → downstream (per expression)
    # Approval gates
    APPROVAL_INPUT = "approval_input"  # upstream → approval node
    APPROVAL_APPROVE = "approval_approve"  # approval → approve path
    APPROVAL_REJECT = "approval_reject"  # approval → reject path
    # While loop
    WHILE_BODY = "while_body"  # while → body (loop back)
    WHILE_DONE = "while_done"  # while → done (exit loop)
    # MCP server
    AGENT_MCP = "agent_mcp"  # mcp_server → agent (tools injection)


# ---------------------------------------------------------------------------
# Position (for canvas rendering — not used at runtime)
# ---------------------------------------------------------------------------


class Position(BaseModel):
    x: float = 0.0
    y: float = 0.0


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------


class NodeConfig(BaseModel):
    """A single node on the visual canvas.

    ``config`` carries type-specific settings — the exact shape depends
    on ``node_type``.  Examples::

        # agent config
        {"model": "gpt-4o-mini", "system_prompt": "...", "max_iterations": 10}

        # tool config
        {"tool_name": "calculator", "hitl_mode": "blocking"}

        # guardrail config
        {"guardrail_type": "output", "schema": "ContentSafetyJudge",
         "pass_field": "safe", "system_prompt": "..."}

        # router config
        {"routing_key": "category", "system_prompt": "...",
         "routing_fields": [{"name": "category", "type": "str", "description": "..."},
                            {"name": "reasoning", "type": "str", "description": "..."}]}

        # memory config
        {"backend": "redis", "ttl": 3600, "max_messages": 200}

        # skill config
        {"skill_name": "web-research"}
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(default_factory=lambda: str(uuid4()))
    node_type: NodeType
    label: str = ""
    position: Position = Field(default_factory=Position)
    config: Dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Edge
# ---------------------------------------------------------------------------


class EdgeConfig(BaseModel):
    """A directed connection between two nodes."""

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(default_factory=lambda: str(uuid4()))
    source: str  # source node id
    target: str  # target node id
    source_handle: str = ""  # output handle on source node
    target_handle: str = ""  # input handle on target node
    edge_type: EdgeType
    label: str = ""  # used for router route labels


# ---------------------------------------------------------------------------
# Pipeline (full graph)
# ---------------------------------------------------------------------------


class PipelineConfig(BaseModel):
    """Complete pipeline definition — the JSON that lives in the DB."""

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str = "Untitled Pipeline"
    description: str = ""
    nodes: List[NodeConfig] = Field(default_factory=list)
    edges: List[EdgeConfig] = Field(default_factory=list)

    # ── helpers ──────────────────────────────────────────────────────────

    def nodes_by_type(self, node_type: NodeType) -> List[NodeConfig]:
        """Return all nodes of a given type."""
        return [n for n in self.nodes if n.node_type == node_type]

    def edges_from(self, node_id: str) -> List[EdgeConfig]:
        """Return all outgoing edges from a node."""
        return [e for e in self.edges if e.source == node_id]

    def edges_to(self, node_id: str) -> List[EdgeConfig]:
        """Return all incoming edges to a node."""
        return [e for e in self.edges if e.target == node_id]

    def node_by_id(self, node_id: str) -> Optional[NodeConfig]:
        """Lookup a node by its id."""
        for n in self.nodes:
            if n.id == node_id:
                return n
        return None
