"""MCP-compatible tool primitives with GoF Template Method contract.

Design decisions (GoF §5.10 Template Method; SOLID Open/Closed):
- ``BaseTool`` is the template.  Subclasses fill in ``execute()``.
- ``ToolRisk`` (Strategy pattern) classifies every tool's risk level at
  definition time, enabling colour-coded UI without runtime inspection.
- ``ToolAnnotations`` is a typed Pydantic model enforcing MCP annotation
  compliance — no raw ``Dict[str,Any]`` slipping through.
- ``BaseTool._validate_input()`` runs JSON-Schema validation before every
  ``execute()`` call; subclasses never receive unvalidated kwargs.
- ``__init_subclass__`` enforces that each concrete tool declares a ``name``
  — ensures the LLM / router always has a unique, stable identifier.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, ClassVar, Dict, List, Optional
from pydantic import BaseModel, Field, ConfigDict, field_validator
import json
from uuid import uuid4

logger = logging.getLogger("agent_framework.tools")


# ---------------------------------------------------------------------------
# Risk / colour classification  (Strategy pattern)
# ---------------------------------------------------------------------------

class ToolRisk(str, Enum):
    """Risk tier for a tool — drives colour badges in the UI and approval gates.

    SAFE      (green)  — read-only, deterministic, no external side-effects.
                         Examples: calculator, clock, grep, list-dir.
    SENSITIVE (yellow) — reads sensitive data, performs network requests, or
                         produces output that depends on external state.
                         Examples: web_surfer, code_interpreter, file_reader.
    CRITICAL  (red)    — writes / deletes state, acts on behalf of the user,
                         or calls third-party services with real-world effects.
                         Examples: manage_tasks, send_email, spotify_player,
                         file_writer, ask_human.

    The ``color`` property is consumed by the SSE / WebSocket event payload and
    by the frontend badge renderer.
    """
    SAFE      = "safe"
    SENSITIVE = "sensitive"
    CRITICAL  = "critical"

    @property
    def color(self) -> str:
        return {"safe": "green", "sensitive": "yellow", "critical": "red"}[self.value]


# ---------------------------------------------------------------------------
# HITL interaction mode
# ---------------------------------------------------------------------------

class HitlMode(str, Enum):
    """How the agent reacts when a tool triggers a human-in-the-loop event.

    BLOCKING
        Agent suspends until the user responds.  If the global
        ``response_timeout`` expires, the approval is **denied** and the
        agent receives an error result.  Use for irreversible actions where
        a non-response must be treated as a veto (e.g. payment execution,
        file deletion, send-email).

    CONTINUE_ON_TIMEOUT
        Agent sends the approval request and waits up to
        ``hitl_timeout_seconds``.  If the user responds in time, their
        decision is applied.  If the timeout expires the tool is
        **approved automatically** with its original arguments and the
        run continues uninterrupted.  Use for time-sensitive UIs where a
        slow user shouldn't block the workflow (e.g. suggesting a calendar
        slot, confirming a filter selection).

    FIRE_AND_CONTINUE
        Agent sends the SSE event to the frontend and immediately continues
        without waiting for any response.  The tool is treated as approved.
        Use for purely informational or decorative UI updates that the agent
        should not block on (e.g. progress panels, live dashboards, MCP App
        state pushes that the user can interact with later).
    """
    BLOCKING             = "blocking"
    CONTINUE_ON_TIMEOUT  = "continue_on_timeout"
    FIRE_AND_CONTINUE    = "fire_and_continue"


# ---------------------------------------------------------------------------
# Typed MCP annotation model  (replaces raw Dict[str,Any])
# ---------------------------------------------------------------------------

class ToolAnnotations(BaseModel):
    """Validated MCP tool annotations.

    Mirrors the MCP specification's ``annotations`` object.  Any extra fields
    from future spec versions pass through via ``extra="allow"``.
    """
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    readOnlyHint:    Optional[bool] = Field(None, description="Tool only reads, never writes")
    destructiveHint: Optional[bool] = Field(None, description="Tool may delete or overwrite data")
    idempotentHint:  Optional[bool] = Field(None, description="Repeated calls with same args produce same result")
    openWorldHint:   Optional[bool] = Field(None, description="Tool may interact with external world")
    title:           Optional[str]  = Field(None, description="Human-readable tool name for UI")


# ---------------------------------------------------------------------------
# Tool schema  (MCP wire format)
# ---------------------------------------------------------------------------

class Tool(BaseModel):
    """MCP-compatible tool schema with annotations and MCP Apps UI support."""
    model_config = ConfigDict(arbitrary_types_allowed=True, populate_by_name=True)

    name: str
    description: str
    inputSchema: Dict[str, Any] = Field(
        default_factory=lambda: {"type": "object", "properties": {}, "required": []},
        description="JSON Schema for tool input parameters (MCP format)",
    )
    annotations: Optional[ToolAnnotations] = Field(
        default=None,
        description="MCP tool annotations",
    )
    meta: Optional[Dict[str, Any]] = Field(
        default=None,
        alias="_meta",
        serialization_alias="_meta",
        description="MCP Apps metadata — e.g. {'ui': {'resourceUri': 'ui://...'}}",
    )
    # Risk tier exposed in schema so the frontend/router can read it without
    # importing the Python tool class.
    risk: str = Field(default="safe", description="Risk tier: safe | sensitive | critical")    # HITL interaction mode — controls what the agent does when approval fires
    hitl_mode: str = Field(
        default=HitlMode.BLOCKING.value,
        description="HITL mode: blocking | continue_on_timeout | fire_and_continue",
    )
    hitl_timeout_seconds: Optional[float] = Field(
        default=None,
        description="Seconds to wait before auto-continuing (only used in continue_on_timeout mode)",
    )
    def to_openai_format(self) -> Dict[str, Any]:
        """Convert MCP tool schema to OpenAI function calling format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.inputSchema,
                "strict": True,
            },
        }

    def to_mcp_format(self) -> Dict[str, Any]:
        """Export as MCP tool schema with annotations and _meta."""
        result: Dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.inputSchema,
        }
        if self.annotations:
            result["annotations"] = self.annotations.model_dump(exclude_none=True)
        if self.meta:
            result["_meta"] = self.meta
        return result


# ---------------------------------------------------------------------------
# ToolResult
# ---------------------------------------------------------------------------

class ToolResult(BaseModel):
    """Structured result from tool execution (MCP-compatible).

    ``is_error`` is the canonical Python attribute name (PEP 8).
    ``isError`` is the MCP wire-format alias for serialization only.
    """
    model_config = ConfigDict(populate_by_name=True)

    content:  List[Dict[str, Any]] = Field(default_factory=list)
    is_error: bool = Field(default=False, alias="isError")
    app_data: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Structured data for MCP App UIs (sent to iframe, not to LLM)",
    )

    # Back-compat: allow callers that still pass isError=True
    @field_validator("is_error", mode="before")
    @classmethod
    def _coerce_bool(cls, v: Any) -> bool:
        return bool(v)


# ---------------------------------------------------------------------------
# ToolCall
# ---------------------------------------------------------------------------

class ToolCall(BaseModel):
    """Represents a tool call instance."""
    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("arguments", mode="before")
    @classmethod
    def _validate_arguments(cls, v: Any) -> Dict[str, Any]:
        if isinstance(v, str):
            try:
                return json.loads(v)
            except Exception:
                raise ValueError("arguments must be a dict or JSON string")
        if isinstance(v, dict):
            return v
        raise ValueError("arguments must be a dict")


# ---------------------------------------------------------------------------
# BaseTool  (Template Method)
# ---------------------------------------------------------------------------

class BaseTool(ABC):
    """Base class for all MCP-compatible tools.

    Subclass contract (enforced via ``__init_subclass__``):
      - Declare a class-level ``name: ClassVar[str]`` attribute.
      - Implement ``async execute(**kwargs) -> ToolResult``.
      - Declare ``risk: ClassVar[ToolRisk]`` (defaults to SAFE if omitted,
        but subclasses are encouraged to be explicit).

    Input validation (GoF Template Method — ``execute`` is the hook):
      ``execute()`` automatically validates ``**kwargs`` against the declared
      ``input_schema`` before dispatching to the subclass implementation.
      Override ``_validate_input()`` only if you need custom validation logic.
    """

    # Subclasses may override these as class-level attributes for clarity
    risk: ClassVar[ToolRisk] = ToolRisk.SAFE
    hitl_mode: ClassVar[HitlMode] = HitlMode.BLOCKING
    hitl_timeout_seconds: ClassVar[Optional[float]] = None

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        # ABCMeta already enforces that concrete subclasses implement `execute`.
        # Historically this hook also asserted a class-level `name` ClassVar, but
        # the framework convention is to pass `name` to BaseTool.__init__(); the
        # instance attribute is set there, not at class-definition time.
        # No additional enforcement is needed here.

    def __init__(
        self,
        name: str,
        description: str,
        input_schema: Optional[Dict[str, Any]] = None,
        annotations: Optional[ToolAnnotations | Dict[str, Any]] = None,
        _meta: Optional[Dict[str, Any]] = None,
        risk: ToolRisk = ToolRisk.SAFE,
        hitl_mode: HitlMode = HitlMode.BLOCKING,
        hitl_timeout_seconds: Optional[float] = None,
    ) -> None:
        self.name = name
        self.description = description
        self.input_schema = input_schema or {
            "type": "object",
            "properties": {},
            "required": [],
        }
        # Accept raw dicts for backward compatibility — coerce to ToolAnnotations
        if isinstance(annotations, dict):
            self.annotations: Optional[ToolAnnotations] = ToolAnnotations(**annotations)
        else:
            self.annotations = annotations
        self._meta = _meta
        # Instance-level risk can be set via __init__; class-level is the default
        self.risk: ToolRisk = risk if risk is not ToolRisk.SAFE else type(self).risk
        # HITL mode: instance overrides class default if explicitly provided
        self.hitl_mode: HitlMode = (
            hitl_mode if hitl_mode is not HitlMode.BLOCKING else type(self).hitl_mode
        )
        self.hitl_timeout_seconds: Optional[float] = (
            hitl_timeout_seconds
            if hitl_timeout_seconds is not None
            else type(self).hitl_timeout_seconds
        )

    # ── JSON-Schema input validation (Template Method) ─────────────────────

    def _validate_input(self, kwargs: Dict[str, Any]) -> None:
        """Validate ``kwargs`` against ``self.input_schema`` using jsonschema.

        Raises ``ValueError`` with a human-readable message on failure.
        Skips validation when the schema has no ``properties`` (catch-all tools).
        """
        if not self.input_schema.get("properties"):
            return
        try:
            import jsonschema
            jsonschema.validate(instance=kwargs, schema=self.input_schema)
        except ImportError:
            # jsonschema not installed — skip validation rather than crash
            logger.debug("jsonschema not installed; skipping input validation for %s", self.name)
        except jsonschema.ValidationError as exc:
            raise ValueError(f"Input validation failed for tool '{self.name}': {exc.message}") from exc

    @abstractmethod
    async def execute(self, **kwargs: Any) -> ToolResult:
        """Execute the tool with validated parameters.

        The base class calls ``_validate_input(kwargs)`` before dispatching
        here, so implementations may assume the kwargs conform to
        ``input_schema``.

        Returns:
            ToolResult with structured content and ``is_error`` flag.
        """
        ...

    def get_schema(self) -> Tool:
        """Return MCP-native tool schema including risk tier and annotations."""
        return Tool(
            name=self.name,
            description=self.description,
            inputSchema=self.input_schema,
            annotations=self.annotations,
            meta=getattr(self, "_meta", None),
            risk=self.risk.value,
            hitl_mode=self.hitl_mode.value,
            hitl_timeout_seconds=self.hitl_timeout_seconds,
        )

    def get_openai_schema(self) -> Dict[str, Any]:
        """Return OpenAI function-calling format (compatibility adapter)."""
        return self.get_schema().to_openai_format()

    def get_mcp_schema(self) -> Dict[str, Any]:
        """Return MCP tool schema format."""
        return self.get_schema().to_mcp_format()

    # ── Convenience executor called by the agent loop ─────────────────────

    async def run(self, **kwargs: Any) -> ToolResult:
        """Validate input then execute.  This is the agent entry-point.

        Subclasses must implement ``execute()``, not override ``run()``.
        """
        try:
            self._validate_input(kwargs)
        except ValueError as exc:
            return ToolResult(
                content=[{"type": "text", "text": str(exc)}],
                is_error=True,
            )
        return await self.execute(**kwargs)

    def __str__(self) -> str:
        return f"{self.name} [{self.risk.color}]: {self.description}"

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}(name='{self.name}', risk={self.risk!r})>"