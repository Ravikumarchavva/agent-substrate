"""GuardrailSpec — first-class guardrail configuration for AssistantAgent.

Instead of manually constructing a ``GuardrailsMiddleware`` and appending it
to the middleware list, pass a ``GuardrailSpec`` directly to the agent
constructor. The agent converts it internally.

Usage::

    from ravi.kernel.guardrails.spec import GuardrailSpec
    from ravi.reasoning.guardrails import PIIDetectionGuardrail, ContentFilterGuardrail

    agent = AssistantAgent(
        "bot", runtime,
        model=llm,
        guardrails=GuardrailSpec(
            input=[PIIDetectionGuardrail()],
            output=[ContentFilterGuardrail()],
        ),
    )
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, TYPE_CHECKING

if TYPE_CHECKING:
    from ravi.kernel.guardrails.base_guardrail import BaseGuardrail


@dataclass
class GuardrailSpec:
    """Named guardrail lists for each interception point.

    Parameters
    ----------
    input:
        Guardrails run before user input reaches the LLM.
    output:
        Guardrails run after the LLM responds (before returning to caller).
    tool_call:
        Guardrails run before each tool execution.
    """

    input: List["BaseGuardrail"] = field(default_factory=list)
    output: List["BaseGuardrail"] = field(default_factory=list)
    tool_call: List["BaseGuardrail"] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.input or self.output or self.tool_call)

    def __repr__(self) -> str:
        return (
            f"<GuardrailSpec("
            f"input={len(self.input)}, "
            f"output={len(self.output)}, "
            f"tool_call={len(self.tool_call)}"
            f")>"
        )
