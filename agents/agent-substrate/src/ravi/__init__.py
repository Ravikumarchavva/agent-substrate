"""ravi — async AI-agent framework.

Quick-start for client apps::

    from ravi import ReActAgent, LocalRuntime, create_model_client
    from ravi import LLMJudgeGuardrail, GuardrailType
    from ravi import TextDelta, CompletionEvent, StreamDone
    from ravi.exceptions import GuardrailTripwireError

All symbols are loaded lazily so importing this package does not trigger
any logging configuration or heavy module initialisation as a side effect.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ravi.adapters.llm.factory import LLMFactory, create_model_client
    from ravi.agents.core.react import AgentRunResult, ReActAgent
    from ravi.agents.context import (
        AgentContext,
        InMemoryHistoryProvider,
        SlidingWindowCompaction,
    )
    from ravi.agents.guardrails import (
        ContentFilterGuardrail,
        GuardrailType,
        LLMJudgeGuardrail,
        MaxTokenGuardrail,
        PIIDetectionGuardrail,
        PromptInjectionGuardrail,
        ToolCallValidationGuardrail,
    )
    from ravi.agents.runtime import LocalRuntime
    from ravi.agents.skills import Skill
    from ravi.exceptions import GuardrailTripwireError
    from ravi.kernel import ChatMessage, TextBlock, ToolExecutionResult
    from ravi.kernel.stream import (
        CompletionEvent,
        ReasoningDelta,
        StreamDone,
        TextDelta,
    )

__all__ = [
    # agents
    "ReActAgent",
    "AgentRunResult",
    "LocalRuntime",
    "Skill",
    "InMemoryHistoryProvider",
    "AgentContext",
    "SlidingWindowCompaction",
    # guardrails
    "ContentFilterGuardrail",
    "GuardrailType",
    "LLMJudgeGuardrail",
    "MaxTokenGuardrail",
    "PIIDetectionGuardrail",
    "PromptInjectionGuardrail",
    "ToolCallValidationGuardrail",
    "GuardrailTripwireError",
    # llm
    "create_model_client",
    "LLMFactory",
    # stream
    "TextDelta",
    "ReasoningDelta",
    "CompletionEvent",
    "StreamDone",
    # kernel types
    "ChatMessage",
    "TextBlock",
    "ToolExecutionResult",
]

_LAZY: dict[str, tuple[str, str]] = {
    "ReActAgent": ("ravi.agents.core.react", "ReActAgent"),
    "AgentRunResult": ("ravi.agents.core.react", "AgentRunResult"),
    "LocalRuntime": ("ravi.agents.runtime", "LocalRuntime"),
    "Skill": ("ravi.agents.skills", "Skill"),
    "InMemoryHistoryProvider": ("ravi.agents.context", "InMemoryHistoryProvider"),
    "AgentContext": ("ravi.agents.context", "AgentContext"),
    "SlidingWindowCompaction": ("ravi.agents.context", "SlidingWindowCompaction"),
    "ContentFilterGuardrail": ("ravi.agents.guardrails", "ContentFilterGuardrail"),
    "GuardrailType": ("ravi.agents.guardrails", "GuardrailType"),
    "LLMJudgeGuardrail": ("ravi.agents.guardrails", "LLMJudgeGuardrail"),
    "MaxTokenGuardrail": ("ravi.agents.guardrails", "MaxTokenGuardrail"),
    "PIIDetectionGuardrail": ("ravi.agents.guardrails", "PIIDetectionGuardrail"),
    "PromptInjectionGuardrail": ("ravi.agents.guardrails", "PromptInjectionGuardrail"),
    "ToolCallValidationGuardrail": (
        "ravi.agents.guardrails",
        "ToolCallValidationGuardrail",
    ),
    "GuardrailTripwireError": ("ravi.exceptions", "GuardrailTripwireError"),
    "create_model_client": ("ravi.adapters.llm.factory", "create_model_client"),
    "LLMFactory": ("ravi.adapters.llm.factory", "LLMFactory"),
    "TextDelta": ("ravi.kernel.stream", "TextDelta"),
    "ReasoningDelta": ("ravi.kernel.stream", "ReasoningDelta"),
    "CompletionEvent": ("ravi.kernel.stream", "CompletionEvent"),
    "StreamDone": ("ravi.kernel.stream", "StreamDone"),
    "ChatMessage": ("ravi.kernel.content", "ChatMessage"),
    "TextBlock": ("ravi.kernel.content", "TextBlock"),
    "ToolExecutionResult": ("ravi.kernel.tools", "ToolExecutionResult"),
}


def __getattr__(name: str) -> object:
    if name in _LAZY:
        import importlib

        module_path, attr = _LAZY[name]
        obj = getattr(importlib.import_module(module_path), attr)
        globals()[name] = obj  # cache — subsequent access is a plain dict lookup
        return obj
    raise AttributeError(f"module 'ravi' has no attribute {name!r}")


def main() -> None:
    """Entry point — run ``uvicorn ravi.serving.monolith.app:app --port 8001 --reload``."""
    print("ravi — run `uvicorn ravi.serving.monolith.app:app --port 8001 --reload`")
