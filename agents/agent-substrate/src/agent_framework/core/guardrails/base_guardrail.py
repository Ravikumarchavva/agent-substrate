"""Guardrail base classes, result models, and context.

A guardrail is an async check that runs at a specific interception point
in the agent run loop.  It inspects a *context* (input text, LLM output,
or tool-call arguments) and returns a *result* that is either:

  • passed   → execution continues
  • failed   → a warning is logged (soft failure)
  • tripwire → execution halts immediately (hard stop)

Design decisions:
  - Guardrails are async so they can call external services (LLM judge, API).
  - Each guardrail declares its *type* (input / output / tool_call) so the
    agent knows *when* to invoke it.
  - GuardrailContext is a frozen snapshot — guardrails never mutate state.
  - Results carry metadata for audit trails.
  - Guardrails run in parallel by default (asyncio.gather) for performance.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class GuardrailType(str, Enum):
    """When a guardrail fires in the agent loop."""
    INPUT = "input"            # Before user input enters memory / LLM
    OUTPUT = "output"          # After LLM responds, before returning to user
    TOOL_CALL = "tool_call"    # Before a tool is executed


# ---------------------------------------------------------------------------
# Context — immutable snapshot passed to guardrails
# ---------------------------------------------------------------------------

class GuardrailContext(BaseModel):
    """Read-only context for a guardrail check.

    Carries everything a guardrail might need to make a decision.
    Fields are Optional because not every field applies to every type.
    """
    # Identity
    agent_name: str = ""
    run_id: str = ""

    # Input guardrail fields
    input_text: Optional[str] = None

    # Output guardrail fields
    output_text: Optional[str] = None
    output_tool_calls: Optional[List[Dict[str, Any]]] = None

    # Tool-call guardrail fields
    tool_name: Optional[str] = None
    tool_arguments: Optional[Dict[str, Any]] = None

    # Full message (for advanced guardrails that inspect raw message)
    raw_message: Optional[Any] = None

    model_config = {"frozen": True}


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

class GuardrailResult(BaseModel):
    """Outcome of a single guardrail check."""
    guardrail_name: str
    guardrail_type: GuardrailType
    passed: bool = True
    tripwire: bool = False        # True → hard stop, agent loop aborts
    message: str = ""             # Human-readable explanation
    metadata: Dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    model_config = {"frozen": False}


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class BaseGuardrail(ABC):
    """Abstract base for all guardrails.

    Subclasses must implement ``check()`` and set ``guardrail_type``.

    Usage::

        class MyGuardrail(BaseGuardrail):
            name = "my_guardrail"
            description = "Blocks bad words"
            guardrail_type = GuardrailType.INPUT

            async def check(self, ctx: GuardrailContext) -> GuardrailResult:
                if "bad" in (ctx.input_text or ""):
                    return GuardrailResult(
                        guardrail_name=self.name,
                        guardrail_type=self.guardrail_type,
                        passed=False,
                        tripwire=True,
                        message="Blocked: contains bad word",
                    )
                return GuardrailResult(
                    guardrail_name=self.name,
                    guardrail_type=self.guardrail_type,
                    passed=True,
                )
    """

    name: str = "base_guardrail"
    description: str = ""
    guardrail_type: GuardrailType = GuardrailType.INPUT

    @abstractmethod
    async def check(self, ctx: GuardrailContext) -> GuardrailResult:
        """Run the guardrail check and return a result.

        Must never raise — wrap errors in a failed GuardrailResult instead.
        """
        ...

    # Convenience helpers ----------------------------------------------------

    def _pass(self, message: str = "", **meta: Any) -> GuardrailResult:
        """Shortcut to build a passing result."""
        return GuardrailResult(
            guardrail_name=self.name,
            guardrail_type=self.guardrail_type,
            passed=True,
            message=message,
            metadata=meta,
        )

    def _fail(self, message: str, *, tripwire: bool = False, **meta: Any) -> GuardrailResult:
        """Shortcut to build a failing result."""
        return GuardrailResult(
            guardrail_name=self.name,
            guardrail_type=self.guardrail_type,
            passed=False,
            tripwire=tripwire,
            message=message,
            metadata=meta,
        )

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}(name={self.name!r}, type={self.guardrail_type.value})>"
