"""Unified public type surface for agent-framework.

Import shared enums and value types from here instead of hunting across submodules.

Usage::

    from agent_framework.core.types import (
        ToolRisk, HitlMode,          # tool config
        GuardrailType,               # guardrail config
        RunStatus,                   # agent run result
        NodeType, EdgeType,          # pipeline graph
    )
"""
from __future__ import annotations

# Tool-related enums — canonical home: core/tools/base_tool.py
from agent_framework.core.tools.base_tool import ToolRisk, HitlMode

# Guardrail enum — canonical home: core/guardrails/base_guardrail.py
from agent_framework.core.guardrails.base_guardrail import GuardrailType

# Agent result enum — canonical home: core/agents/agent_result.py
from agent_framework.core.agents.agent_result import RunStatus

# Pipeline enums — canonical home: core/pipelines/schema.py
from agent_framework.core.pipelines.schema import NodeType, EdgeType

__all__ = [
    "ToolRisk",
    "HitlMode",
    "GuardrailType",
    "RunStatus",
    "NodeType",
    "EdgeType",
]
