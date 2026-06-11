"""Tool contracts — the Tool Protocol, ToolRisk, ToolType, ToolUI, ToolRegistry."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol

from ravi.kernel.content import JsonObject


class ToolRisk(str, Enum):
    """Risk classification for a tool.

    SAFE     — no side-effects; execute without approval.
    HIGH     — external side-effects (email, DB write); may require approval.
    CRITICAL — destructive / irreversible; always requires approval.
    """

    SAFE = "safe"
    HIGH = "high"
    CRITICAL = "critical"


class ToolType(str, Enum):
    """Category classification for a tool.

    Used for discovery grouping, dashboard display, and audit logging.
    Not used for LLM-provider routing.
    """

    FUNCTION = "function"
    SKILL = "skill"
    MCP = "mcp"
    A2A = "a2a"
    KNOWLEDGE = "knowledge"
    CONNECTOR = "connector"
    PIPELINE = "pipeline"


@dataclass(frozen=True)
class ToolUI:
    """Declares that a tool renders through an MCP-App UI resource.

    ``resource_uri``  the ``ui://name`` resource that renders this tool.
    ``csp``           opaque CSP hints for the host (passed through as-is).
    ``permissions``   sandbox capabilities to request.
    ``prefers_border`` host hint to draw a visual boundary.

    ``csp`` is intentionally typed as ``JsonObject`` so the kernel does not
    encode any specific CSP schema — that is a host/adapter concern.
    """

    resource_uri: str
    csp: JsonObject | None = None
    permissions: tuple[str, ...] = field(default_factory=tuple)
    prefers_border: bool = False


# ---------------------------------------------------------------------------
# ToolExecutionResult — canonical tool result type
# ---------------------------------------------------------------------------

from ravi.kernel.message import ToolResultPayload as ToolExecutionResult


# ---------------------------------------------------------------------------
# Tool Protocol
# ---------------------------------------------------------------------------


class Tool(Protocol):
    """Contract every tool must satisfy.

    ``risk`` defaults to ``ToolRisk.SAFE`` when absent.
    ``ui`` is an optional ``ToolUI`` declaration.
    ``tool_type`` defaults to ``ToolType.FUNCTION`` when absent.

    ``execute`` receives keyword arguments matching the tool's ``input_schema``
    and returns a ``ToolExecutionResult``.
    """

    name: str
    description: str
    input_schema: dict[str, object]

    async def execute(self, **kwargs: object) -> ToolExecutionResult: ...


# ---------------------------------------------------------------------------
# ToolCallRequest — canonical tool-call request type (lives here, not message.py)
# ---------------------------------------------------------------------------

from ravi.kernel.message import ToolCallPayload as ToolCallRequest


# ---------------------------------------------------------------------------
# ToolRegistry Protocol
# ---------------------------------------------------------------------------


class ToolRegistry(Protocol):
    """Contract for a name-keyed collection of Tool instances.

    Implementations may use an in-memory dict (see agents layer), a database,
    or a remote catalog — the agent loop only needs these four operations.
    """

    def get(self, name: str) -> Tool | None: ...

    def all(self) -> list[Tool]: ...

    def names(self) -> list[str]: ...

    def add(self, tool: Tool) -> None: ...


__all__ = [
    "ToolRisk",
    "ToolType",
    "ToolUI",
    "ToolExecutionResult",
    "ToolCallRequest",
    "Tool",
    "ToolRegistry",
]
