"""Resource specification types for the agent catalog.

Every entity registered in ``AgentCatalog`` is described by a
``ResourceSpec`` — a typed, immutable metadata model that carries the
resource's identity, type, and governance attributes.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, List, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, Field


@runtime_checkable
class SkillManagerProtocol(Protocol):
    """Structural contract a skill manager must satisfy to be attached to ``AgentCatalog``.

    The kernel does not know about the concrete ``SkillManager`` implementation
    in ``ravi.catalog`` — callers construct the manager and inject it via
    :meth:`AgentCatalog.init_skills`.
    """

    def activate(self, name: str) -> Optional[Any]: ...
    def active_context_block(self) -> str: ...
    def available_skills_xml(self) -> str: ...
    def inject_into_prompt(self, system_prompt: str) -> str: ...


class ResourceType(str, Enum):
    """Typed classification for catalog resources."""

    TOOL = "tool"
    SKILL = "skill"
    MEMORY = "memory"
    CONTEXT = "context"
    CHECKPOINT = "checkpoint"
    MCP_TOOL = "mcp_tool"  # Sprint 5: MCP adapter layer
    MODEL = "model"


class ResourceSpec(BaseModel):
    """Typed, immutable specification for a catalog resource.

    FQN format: ``{catalog}.{schema}.{name}``  (all lower-case)

    Examples::

        ResourceSpec(name="tax_calculator", namespace="main.finance", resource_type=ResourceType.TOOL)
        # → fqn = "main.finance.tax_calculator"
    """

    name: str
    namespace: str  # "catalog.schema" prefix
    resource_type: ResourceType
    description: str = ""
    version: str = "1.0.0"
    tags: List[str] = Field(default_factory=list)
    category: str = ""
    aliases: List[str] = Field(default_factory=list)

    model_config = {"frozen": True}

    @property
    def fqn(self) -> str:
        """Fully-qualified name: ``{namespace}.{name}`` (lower-case)."""
        return f"{self.namespace}.{self.name}".lower()

    @classmethod
    def for_tool(
        cls,
        name: str,
        *,
        catalog: str = "main",
        schema: str = "default",
        description: str = "",
        category: str = "",
        tags: List[str] | None = None,
        aliases: List[str] | None = None,
    ) -> "ResourceSpec":
        return cls(
            name=name,
            namespace=f"{catalog}.{schema}",
            resource_type=ResourceType.TOOL,
            description=description,
            category=category,
            tags=tags or [],
            aliases=aliases or [],
        )

    @classmethod
    def for_skill(
        cls,
        name: str,
        *,
        catalog: str = "main",
        schema: str = "default",
        description: str = "",
        tags: List[str] | None = None,
    ) -> "ResourceSpec":
        return cls(
            name=name,
            namespace=f"{catalog}.{schema}",
            resource_type=ResourceType.SKILL,
            description=description,
            tags=tags or [],
        )
