"""Local guardrail contracts — replaces deleted kernel.guardrails.base_guardrail."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class GuardrailType(str, Enum):
    INPUT = "input"
    OUTPUT = "output"
    TOOL_CALL = "tool_call"


@dataclass
class GuardrailContext:
    """Snapshot passed to every guardrail check."""

    agent_name: str = ""
    run_id: str = ""
    input_text: str | None = None
    output_text: str | None = None
    tool_name: str | None = None
    tool_arguments: dict[str, Any] | None = None
    raw_message: object | None = None


@dataclass
class GuardrailResult:
    """Result of a single guardrail check."""

    guardrail_name: str
    guardrail_type: GuardrailType
    passed: bool
    message: str = ""
    tripwire: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


def _pass(name: str, guardrail_type: GuardrailType, message: str = "", **meta: Any) -> GuardrailResult:
    return GuardrailResult(
        guardrail_name=name,
        guardrail_type=guardrail_type,
        passed=True,
        message=message,
        tripwire=False,
        metadata=dict(meta),
    )


def _fail(
    name: str,
    guardrail_type: GuardrailType,
    message: str = "",
    *,
    tripwire: bool = True,
    **meta: Any,
) -> GuardrailResult:
    return GuardrailResult(
        guardrail_name=name,
        guardrail_type=guardrail_type,
        passed=False,
        message=message,
        tripwire=tripwire,
        metadata=dict(meta),
    )
