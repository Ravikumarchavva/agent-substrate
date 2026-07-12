"""Tool contracts — the complete tool taxonomy.

Tool execution model (two orthogonal axes)
-------------------------------------------

                     WHO DECLARES
                  Developer  │  Provider
                  ───────────┼──────────────────────
 WHO    Developer │  Tool     │  ProviderDefinedTool
 EXECUTES Provider │  HostedTool (n/a — not useful) │

``Tool`` (LOCAL)
    Standard function tools.  Developer declares the JSON schema; the agent
    loop calls ``tool.execute(**kwargs)`` locally.  Schema is sent to the
    provider as a ``function`` entry.  Dynamic/lazy tool discovery (avoiding
    a full-schema context dump) is handled by ``ToolSearchTool`` and
    ``SkillTool`` (capabilities layer), not by this contract.

``HostedTool`` (PROVIDER)
    Provider declares and executes.  The agent loop never calls ``execute()``;
    the provider runs it natively and returns results as a regular turn.
    Examples: OpenAI ``web_search_preview``, ``code_interpreter``,
    ``file_search``, ``image_generation``.
    Uses ``provider_specs: dict[provider_id, JsonObject]`` so that the
    FallbackClient can switch providers without sending a malformed spec.

``ProviderDefinedTool`` (PROVIDER_DEFINED)
    Provider declares the *call shape*; the developer executes locally.
    The model emits typed call items (e.g. ``shell_call``); the agent loop
    routes them to ``handle_call(call, ctx=ctx)`` and returns the matching
    output item.
    Examples: OpenAI local ``shell``, ``apply_patch``, ``computer_use``.

``ToolRegistry`` and ``Toolbox`` accept ``AnyTool = Tool | HostedTool | ProviderDefinedTool``.

Use ``is_hosted_tool`` / ``is_provider_defined_tool`` to branch at dispatch time.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import (
    TYPE_CHECKING,
    Any,
    Literal,
    Protocol,
    TypeGuard,
)

from pydantic import BaseModel, Field

from substrate.kernel.core.content import (
    ContentBlock,
    JsonObject,
    content_blocks_to_str,
)

if TYPE_CHECKING:
    from substrate.kernel.agent.runtime_context import RunMeta


# ---------------------------------------------------------------------------
# Risk / type / execution enums
# ---------------------------------------------------------------------------


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
    Not used for LLM-provider routing — use ``ToolExecution`` for that.
    """

    FUNCTION = "function"
    SKILL = "skill"
    MCP = "mcp"
    A2A = "a2a"
    KNOWLEDGE = "knowledge"
    CONNECTOR = "connector"
    PIPELINE = "pipeline"


# ---------------------------------------------------------------------------
# ToolUI
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolUI:
    """Declares that a tool renders through an MCP-App UI resource.

    ``resource_uri``  the ``ui://name`` resource that renders this tool.
    ``csp``           opaque CSP hints for the host (passed through as-is).
    ``permissions``   sandbox capabilities to request.
    ``prefers_border`` host hint to draw a visual boundary.
    """

    resource_uri: str
    csp: JsonObject | None = None
    permissions: tuple[str, ...] = field(default_factory=tuple)
    prefers_border: bool = False


# ---------------------------------------------------------------------------
# PayloadBase — shared base class for all Message payload types
# ---------------------------------------------------------------------------


class PayloadBase(BaseModel):
    """Base class for all types that can be carried as a ``Message`` payload.

    Subclasses must supply a ``kind: Literal[...]`` field so the payload
    registry can dispatch deserialization by discriminator string.
    """

    kind: str
    model_config = {"frozen": True}


# ---------------------------------------------------------------------------
# ToolCallRequest — canonical tool-call request type
# ---------------------------------------------------------------------------


class ToolCallRequest(PayloadBase):
    """A request to execute a named tool.

    ``call_id`` is generated automatically so callers don't need to
    track ids; the agent loop stamps it into the corresponding
    ``ToolExecutionResult`` when the tool completes.
    """

    # Narrowing PayloadBase.kind (str) to a Literal is a known pyright/pydantic
    # limitation — see ChatPayload in kernel/messaging/message.py for detail.
    kind: Literal["tool_call"] = "tool_call"  # pyright: ignore[reportIncompatibleVariableOverride]
    name: str
    arguments: JsonObject = Field(default_factory=dict)
    call_id: str = Field(default_factory=lambda: str(uuid.uuid4()))

    model_config = {"frozen": True}


# ---------------------------------------------------------------------------
# ToolExecutionResult — canonical tool result type
# ---------------------------------------------------------------------------


class ToolExecutionResult(PayloadBase):
    """Result of a single tool execution.

    ``call_id`` defaults to ``""`` so tools can construct results without
    knowing the call id upfront — the agent loop fills it in after dispatch.

    ``content`` is ``list[ContentBlock]`` — same multimodal primitive used
    everywhere in the kernel.  Use ``result.text`` for a plain-text
    representation suitable for LLM context.
    """

    kind: Literal["tool_result"] = "tool_result"  # pyright: ignore[reportIncompatibleVariableOverride]
    call_id: str = ""
    name: str = ""
    content: list[ContentBlock] = Field(default_factory=list)
    is_error: bool = False
    metadata: JsonObject = Field(default_factory=dict)
    structured_content: JsonObject = Field(default_factory=dict)

    model_config = {"frozen": True, "arbitrary_types_allowed": True}

    @property
    def text(self) -> str:
        """Plain-text rendering of all content blocks."""
        return content_blocks_to_str(self.content)


# ---------------------------------------------------------------------------
# Tool Protocol — LOCAL execution
# ---------------------------------------------------------------------------


class Tool(Protocol):
    """Contract every locally-executed tool must satisfy.

    ``risk`` defaults to ``ToolRisk.SAFE`` when absent.
    ``ui`` is an optional ``ToolUI`` declaration.
    ``tool_type`` defaults to ``ToolType.FUNCTION`` when absent.

    ``execute`` receives keyword arguments matching the tool's ``input_schema``
    and returns a ``ToolExecutionResult``.  ``ctx`` carries the execution
    deadline and cancellation token.
    """

    name: str
    description: str
    input_schema: dict[str, object]

    async def execute(
        self, *, ctx: RunMeta | None = None, **kwargs: Any
    ) -> ToolExecutionResult: ...


# ---------------------------------------------------------------------------
# HostedTool Protocol — PROVIDER execution
# ---------------------------------------------------------------------------


class HostedTool(Protocol):
    """Contract for tools executed natively by the LLM provider.

    The agent loop never calls ``execute()`` — the provider runs the tool
    and the result appears as a regular turn in the conversation.

    ``provider_specs`` is a dict keyed by provider id (``"openai"``,
    ``"anthropic"``, ``"gemini"``).  Each LLM encoder picks the entry for
    its own provider.  An absent key means the tool is dropped for that
    provider (never sent malformed — that would cause a provider API 400
    and break FallbackClient failover).

    Examples::

        # OpenAI web search
        WebSearchTool(provider_specs={
            "openai": {"type": "web_search_preview", "search_context_size": "medium"},
        })

        # Multi-provider file search
        FileSearchTool(provider_specs={
            "openai": {"type": "file_search", "vector_store_ids": ["vs_abc"]},
            "anthropic": {"type": "web_search_20250305", "name": "web_search"},
        })
    """

    name: str
    description: str
    provider_specs: dict[str, JsonObject]


# ---------------------------------------------------------------------------
# ProviderDefinedTool Protocol — PROVIDER_DEFINED execution
# ---------------------------------------------------------------------------


class ProviderDefinedTool(Protocol):
    """Contract for tools with provider-declared call shapes, locally executed.

    The provider declares the call schema (e.g. OpenAI ``shell_call``);
    the model emits a typed call item; the agent loop routes it to
    ``handle_call(call, ctx=ctx)`` and returns the matching output item.
    The developer executes the call locally.

    Examples: OpenAI local ``shell``, ``apply_patch``, ``computer_use``.

    ``provider_specs`` — advertised spec per provider (same multi-provider
    keying as ``HostedTool``).
    ``call_types`` — response item type strings this tool handles
    (e.g. ``("shell_call",)``).
    ``handle_call`` — receives the raw provider call item and returns the
    corresponding output item.
    """

    name: str
    description: str
    provider_specs: dict[str, JsonObject]
    call_types: tuple[str, ...]

    async def handle_call(
        self, call: JsonObject, *, ctx: RunMeta | None = None
    ) -> JsonObject: ...


# ---------------------------------------------------------------------------
# AnyTool alias + TypeGuard helpers
# ---------------------------------------------------------------------------

AnyTool = Tool | HostedTool | ProviderDefinedTool
"""Union of all tool execution modes accepted by ``ToolRegistry``."""


def is_provider_defined_tool(tool: object) -> TypeGuard[ProviderDefinedTool]:
    """Return ``True`` when *tool* is a provider-defined, locally-executed tool.

    A ``ProviderDefinedTool`` has ``provider_specs`` AND ``handle_call``.
    Check this *before* ``is_hosted_tool`` when you need to distinguish between
    the two, since both have ``provider_specs``.
    """
    return hasattr(tool, "provider_specs") and hasattr(tool, "handle_call")


def is_hosted_tool(tool: object) -> TypeGuard[HostedTool]:
    """Return ``True`` when *tool* is a provider-hosted tool.

    A ``HostedTool`` has ``provider_specs`` and does NOT have ``handle_call``.
    Use ``is_provider_defined_tool`` first when you need to distinguish between
    ``HostedTool`` and ``ProviderDefinedTool``.

    Use at dispatch time to skip calling ``execute()``::

        if is_hosted_tool(tool):
            # provider handles it; result comes back in conversation
            ...
        elif is_provider_defined_tool(tool):
            result_item = await tool.handle_call(call, ctx=ctx)
        else:
            result = await tool.execute(ctx=ctx, **arguments)
    """
    return hasattr(tool, "provider_specs") and not hasattr(tool, "handle_call")


# ---------------------------------------------------------------------------
# ToolRegistry Protocol
# ---------------------------------------------------------------------------


class ToolRegistry(Protocol):
    """Contract for a name-keyed collection of AnyTool instances.

    Implementations may use an in-memory dict (see agents layer), a database,
    or a remote catalog — the agent loop only needs these four operations.
    All three execution modes (LOCAL, PROVIDER, PROVIDER_DEFINED) may be stored.
    """

    def get(self, name: str) -> AnyTool | None: ...

    def all(self) -> list[AnyTool]: ...

    def names(self) -> list[str]: ...

    def add(self, tool: AnyTool) -> None: ...


__all__ = [
    "ToolRisk",
    "ToolType",
    "ToolUI",
    "ToolCallRequest",
    "ToolExecutionResult",
    "Tool",
    "HostedTool",
    "ProviderDefinedTool",
    "AnyTool",
    "is_hosted_tool",
    "is_provider_defined_tool",
    "ToolRegistry",
]
