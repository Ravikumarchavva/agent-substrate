"""Temporary stubs for types deleted in the kernel/fabric migration.

These allow assistant agent files to import while the full agent
migration (agent body + helpers) is done in a separate pass.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class RunStatus(str, Enum):
    SUCCESS = "success"
    ERROR = "error"
    GUARDRAIL_TRIPPED = "guardrail_tripped"
    MAX_ITERATIONS = "max_iterations"
    CANCELLED = "cancelled"


@dataclass
class AggregatedUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0


@dataclass
class ToolCallRecord:
    tool_name: str = ""
    call_id: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    result: Any = None
    is_error: bool = False
    duration_ms: float = 0.0


@dataclass
class StepResult:
    step_number: int = 0
    type: str = ""
    content: Any = None
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    usage: AggregatedUsage = field(default_factory=AggregatedUsage)


@dataclass
class AgentRunResult:
    run_id: str = ""
    agent_name: str = ""
    output: Any = None
    status: RunStatus = RunStatus.SUCCESS
    steps: list[StepResult] = field(default_factory=list)
    usage: AggregatedUsage = field(default_factory=AggregatedUsage)
    start_time: Any = None
    end_time: Any = None
    duration_seconds: float = 0.0
    max_iterations: int = 0
    error: str | None = None
    guardrail_results: list[Any] = field(default_factory=list)
    structured_output: Any = None
    tool_calls_total: int = 0
    tool_calls_by_name: dict[str, int] = field(default_factory=dict)

    def model_dump(self, **kwargs: Any) -> dict[str, Any]:
        return {}


@dataclass
class ParsedToolCall:
    name: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    call_id: str = ""


class MemoryScope(str, Enum):
    ISOLATED = "isolated"
    SHARED = "shared"
    GLOBAL = "global"


# Stub for deleted message types
class _StubMessage:
    role: str = ""
    content: Any = None
    tool_calls: list[Any] = field(default_factory=list)

    def __init__(self, **kwargs: Any) -> None:
        for k, v in kwargs.items():
            setattr(self, k, v)


AssistantMessage = _StubMessage
ToolExecutionResultMessage = _StubMessage
SystemMessage = _StubMessage
UserMessage = _StubMessage

# Stub for deleted context types
class ExecutionContext:
    def __init__(self, **kwargs: Any) -> None:
        for k, v in kwargs.items():
            setattr(self, k, v)


class CheckpointStore:
    pass


class ImageContent:
    def __init__(self, data: bytes, media_type: str) -> None:
        self.data = data
        self.media_type = media_type


MediaType = Any


class ReasoningDeltaChunk:
    def __init__(self, text: str = "") -> None:
        self.text = text


class TextDeltaChunk:
    def __init__(self, text: str = "") -> None:
        self.text = text


class CompletionChunk:
    def __init__(self, message: Any = None) -> None:
        self.message = message


class MemoryScopeEnum(str, Enum):
    SESSION = "session"
    GLOBAL = "global"


@dataclass
class RetryPolicy:
    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 30.0
    backoff_factor: float = 2.0
    jitter: float = 1.0
    retryable_exceptions: tuple[type[Exception], ...] = (Exception,)


class ToolApprovalHandler:
    """Stub for HITL tool approval — actual implementation is L4 concern."""
    pass


class PersistentHistoryProvider:
    pass


class LineageNotFoundError(Exception):
    pass


class StorageTier(str, Enum):
    WARM = "warm"
    COLD = "cold"


@dataclass
class ProvenanceTag:
    agent_fqn: str
    activation_id: str
    timestamp_utc: str
    tool_call_id: str | None
    parent_message_id: str | None
    trust_score: float | None


@dataclass
class LineageRecord:
    session_id: str
    message_id: str
    provenance: ProvenanceTag
    tier: StorageTier = StorageTier.WARM


class LineageStore:
    pass


