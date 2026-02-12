"""Agent execution result structures.

Design principles:
- Zero duplication: no field should be derivable from another
- Fully serializable: every result can round-trip through JSON
- Run identity: every run gets a UUID for tracking/resumability
- Status enum: captures all terminal states, not just success/fail
- Multimodal: supports text, images, audio, video outputs
"""
from __future__ import annotations

from enum import Enum
from typing import List, Optional, Any, Dict, Union
from pydantic import BaseModel, Field, computed_field
from datetime import datetime
from uuid import uuid4
from PIL import Image

from agent_framework.messages.base_message import UsageStats
from agent_framework.messages._types import MediaType, AudioContent, VideoContent


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class RunStatus(str, Enum):
    """Terminal status of an agent run."""
    COMPLETED = "completed"                     # Agent finished naturally
    MAX_ITERATIONS = "max_iterations_reached"   # Hit iteration ceiling
    ERROR = "error"                             # Unrecoverable error
    CANCELLED = "cancelled"                     # Externally cancelled
    GUARDRAIL_TRIPPED = "guardrail_tripped"     # Hard-stopped by a guardrail


# ---------------------------------------------------------------------------
# Tool call record (single execution)
# ---------------------------------------------------------------------------

class ToolCallRecord(BaseModel):
    """Record of a single tool invocation and its result."""
    tool_name: str
    call_id: str
    arguments: Dict[str, Any] = Field(default_factory=dict)
    result: str = ""
    is_error: bool = False
    duration_ms: Optional[float] = None          # wall-clock for this call
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    model_config = {"frozen": False}


# ---------------------------------------------------------------------------
# Per-step snapshot
# ---------------------------------------------------------------------------

class StepResult(BaseModel):
    """One think-act cycle inside an agent run.
    
    - If the LLM answered directly (no tool calls), tool_calls is empty.
    - If the LLM requested tools, tool_calls contains one entry per call.
    - Supports multimodal thought (text, images, audio, video).
    """
    step: int                                     # 1-based
    thought: Optional[List[MediaType]] = None    # LLM output (can be multimodal)
    tool_calls: List[ToolCallRecord] = Field(default_factory=list)
    usage: Optional[UsageStats] = None           # tokens for *this* step's LLM call
    finish_reason: str = "stop"                  # stop | tool_calls | error

    model_config = {"frozen": False, "arbitrary_types_allowed": True}

    @computed_field
    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0
    
    @computed_field
    @property
    def thought_text(self) -> Optional[str]:
        """Extract plain text from thought (convenience accessor)."""
        if not self.thought:
            return None
        parts = []
        for item in self.thought:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, (AudioContent, VideoContent)):
                parts.append(f"[{item.__class__.__name__}]")
            elif isinstance(item, Image.Image):
                parts.append("[Image]")
        return " ".join(parts) if parts else None


# ---------------------------------------------------------------------------
# Aggregated usage
# ---------------------------------------------------------------------------

class AggregatedUsage(BaseModel):
    """Accumulated token usage across all LLM calls in a run."""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    llm_calls: int = 0                            # how many generate() calls

    def add(self, usage: Optional[UsageStats]) -> None:
        if usage:
            self.prompt_tokens += usage.prompt_tokens
            self.completion_tokens += usage.completion_tokens
            self.total_tokens += usage.total_tokens
            self.llm_calls += 1


# ---------------------------------------------------------------------------
# Top-level run result
# ---------------------------------------------------------------------------

class AgentRunResult(BaseModel):
    """Complete result of an agent.run() invocation.
    
    Design:
    - ``output`` is the final answer (can be multimodal: text, images, audio, video).
    - ``steps`` is the full reasoning trace (think-act cycles).
    - ``usage`` is the aggregated token spend.
    - No ``conversation_history`` -- reconstruct from steps if needed.
    - No ``final_message`` object -- access via steps[-1] if needed.
    """
    # Identity
    run_id: str = Field(default_factory=lambda: str(uuid4()))
    agent_name: str

    # Output (multimodal)
    output: List[MediaType] = Field(default_factory=list)  # Can contain text, images, audio, video
    status: RunStatus = RunStatus.COMPLETED

    # Execution trace
    steps: List[StepResult] = Field(default_factory=list)

    # Usage
    usage: AggregatedUsage = Field(default_factory=AggregatedUsage)

    # Tool summary (computed at build time for fast access)
    tool_calls_total: int = 0
    tool_calls_by_name: Dict[str, int] = Field(default_factory=dict)

    # Timing
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    duration_seconds: Optional[float] = None

    # Error (only when status == ERROR)
    error: Optional[str] = None

    # Guardrail audit trail
    guardrail_results: List[Any] = Field(default_factory=list)

    # Config snapshot
    max_iterations: int = 0

    model_config = {"frozen": False, "arbitrary_types_allowed": True}

    # -- Convenience ----------------------------------------------------------

    @computed_field
    @property
    def steps_used(self) -> int:
        return len(self.steps)

    @computed_field
    @property
    def success(self) -> bool:
        return self.status == RunStatus.COMPLETED
    
    @computed_field
    @property
    def output_text(self) -> str:
        """Extract plain text from multimodal output (convenience accessor).
        
        For text-only results, this is just the joined text.
        For multimodal results, non-text items are represented as [Type] placeholders.
        """
        if not self.output:
            return ""
        parts = []
        for item in self.output:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, AudioContent):
                parts.append(f"[Audio: {item.format}]")
            elif isinstance(item, VideoContent):
                parts.append(f"[Video: {item.format}]")
            elif isinstance(item, Image.Image):
                parts.append(f"[Image: {item.size[0]}x{item.size[1]}]")
        return " ".join(parts)
    
    @computed_field
    @property
    def has_media(self) -> bool:
        """Check if output contains non-text media (images, audio, video)."""
        return any(not isinstance(item, str) for item in self.output)
    
    @computed_field
    @property
    def media_types(self) -> List[str]:
        """List of media types present in output."""
        types = set()
        for item in self.output:
            if isinstance(item, str):
                types.add("text")
            elif isinstance(item, Image.Image):
                types.add("image")
            elif isinstance(item, AudioContent):
                types.add("audio")
            elif isinstance(item, VideoContent):
                types.add("video")
        return sorted(types)

    def to_dict(self) -> Dict[str, Any]:
        """Full JSON-serializable snapshot for persistence / API responses."""
        return self.model_dump(mode="json")

    def summary(self) -> str:
        """One-line human-readable summary."""
        tool_info = ", ".join(f"{n}x{c}" for n, c in self.tool_calls_by_name.items()) or "none"
        duration = f"{self.duration_seconds:.2f}s" if self.duration_seconds else "n/a"
        media_info = f" | media: {'+'.join(self.media_types)}" if self.has_media else ""
        return (
            f"[{self.status.value}] {self.agent_name} | "
            f"{self.steps_used}/{self.max_iterations} steps | "
            f"{self.tool_calls_total} tool calls ({tool_info}) | "
            f"{self.usage.total_tokens} tokens | "
            f"{duration}{media_info}"
        )

    def __str__(self) -> str:
        lines = [
            f"Run:      {self.run_id}",
            f"Agent:    {self.agent_name}",
            f"Status:   {self.status.value}",
            f"Steps:    {self.steps_used}/{self.max_iterations}",
            f"Tools:    {self.tool_calls_total} calls",
            f"Tokens:   {self.usage.total_tokens} (prompt={self.usage.prompt_tokens}, completion={self.usage.completion_tokens})",
        ]
        if self.duration_seconds is not None:
            lines.append(f"Duration: {self.duration_seconds:.2f}s")
        
        # Output preview
        if self.has_media:
            lines.append(f"Output:   [Multimodal: {', '.join(self.media_types)}]")
            output_preview = self.output_text[:120] + ("..." if len(self.output_text) > 120 else "")
            lines.append(f"          {output_preview}")
        else:
            output_preview = self.output_text[:120] + ("..." if len(self.output_text) > 120 else "")
            lines.append(f"Output:   {output_preview}")
        
        if self.error:
            lines.append(f"Error:    {self.error}")
        return "\n".join(lines)

    def __repr__(self) -> str:
        return f"<AgentRunResult run_id={self.run_id!r} status={self.status.value!r} steps={self.steps_used}>"
