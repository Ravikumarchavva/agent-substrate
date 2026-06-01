"""Local middleware contracts — replaces deleted kernel.middleware.base."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class MiddlewareStage(str, Enum):
    LLM_CALL = "llm_call"
    TOOL_EXECUTION = "tool_execution"


@dataclass
class MiddlewareContext:
    """Snapshot passed through the middleware pipeline for one agent action."""

    agent_name: str = ""
    run_id: str = ""
    stage: MiddlewareStage = MiddlewareStage.LLM_CALL
    input_text: str = ""
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    response_schema: type | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
