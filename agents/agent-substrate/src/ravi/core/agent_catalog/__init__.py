"""core.agent_catalog — unified resource governance for the agent runtime.

Single source of truth for all registered resources (tools, skills, memories,
contexts, checkpoints, MCP tools, models).  Replaces the old god-object
``core/catalog/registry.py``.

Import from here, not from the private ``_*.py`` submodules.
"""

from __future__ import annotations

from ravi.core.agent_catalog._spec import ResourceSpec, ResourceType
from ravi.core.agent_catalog._catalog import AgentCatalog, AgentCatalogRegistry

__all__ = [
    "ResourceSpec",
    "ResourceType",
    "AgentCatalog",
    "AgentCatalogRegistry",   # backward-compat alias
]
