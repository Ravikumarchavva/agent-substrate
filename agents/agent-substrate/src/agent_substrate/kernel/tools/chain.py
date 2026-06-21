"""Chain contracts — shared value types and protocols for sandboxed tool chaining.

Tool chaining allows the LLM to write one Python script that calls multiple
tools and pipes results between them.  The script runs in the existing
CodeInterpreter Firecracker/K8s sandbox; every tool call is routed back to
the framework-side ``ToolInvoker`` (agents layer) for risk/approval/ctx
enforcement.

This file is kernel-pure: no I/O, no concrete implementations.
``ToolInvoker`` (L1/agents), the bridge, prelude, and ``ToolChainTool``
(L2/capabilities) live above this layer.

Data plane overview
-------------------
Small results (< ``ChainPolicy.max_inline_result_bytes``)
    → inline in the sandbox variable

Large results
    → ``ArtifactStore`` (Redis/S3 via ``DataRefStore``); sandbox holds a
      lightweight ``InvocationResult`` handle with ref + preview + summary.
      Passing the handle to the next tool sends only the ref across the
      bridge; ``ToolInvoker`` resolves store→tool directly.

Media blocks (ImageBlock, DocumentBlock)
    → stored as artifacts, listed in ``InvocationResult.files``;
      reconstructed as real ``ContentBlock``s in the final
      ``ToolExecutionResult`` so the model can see the chart.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from agent_substrate.kernel.tools.tools import ToolRisk

# ---------------------------------------------------------------------------
# ChainPolicy — per-chain execution limits
# ---------------------------------------------------------------------------


class ChainPolicy(BaseModel):
    """Execution policy for a single chain run.

    Defaults are intentionally conservative; callers may relax them per use-case.
    ``approval_timeout_s`` must be well below ``call_timeout_s`` so that a
    human taking too long to approve simply returns ``status="denied"`` with
    guidance — the sandbox never blocks indefinitely.
    """

    max_tool_calls: int = 50
    call_timeout_s: float = 60.0
    approval_timeout_s: float = 55.0
    total_timeout_s: float = 300.0
    max_inline_result_bytes: int = 4096
    max_risk_unapproved: ToolRisk = ToolRisk.SAFE

    model_config = {"frozen": True}


# ---------------------------------------------------------------------------
# ChainFile — a media output mapped into the sandbox workspace
# ---------------------------------------------------------------------------


class ChainFile(BaseModel):
    """A media ``ContentBlock`` materialised as a file in the sandbox workspace.

    ``path`` is the absolute path inside the sandbox (pandas-able, etc.).
    ``media_type`` mirrors the original ``ContentBlock`` media type.
    ``artifact_ref`` is the ``ArtifactStore`` ref for the raw bytes.
    """

    path: str
    media_type: str
    artifact_ref: str

    model_config = {"frozen": True}


# ---------------------------------------------------------------------------
# InvocationResult — wire form of a tool result for chain consumption
# ---------------------------------------------------------------------------


class InvocationResult(BaseModel):
    """Result of a single tool invocation as seen by the sandbox.

    Crosses the bridge (control-channel or HTTP) back into the sandbox after
    ``ToolInvoker`` runs the tool.

    ``status``       — ``"ok"`` | ``"error"`` | ``"denied"``
    ``text``         — inline text (or first-N-chars preview when offloaded)
    ``structured``   — ``structured_content`` (or summary dict when offloaded)
    ``artifact_ref`` — set when result was offloaded to ``ArtifactStore``
    ``files``        — media blocks materialised as sandbox workspace files
    """

    status: Literal["ok", "error", "denied"]
    text: str = ""
    structured: dict[str, object] = Field(default_factory=dict)
    artifact_ref: str | None = None
    files: list[ChainFile] = Field(default_factory=list)

    model_config = {"frozen": True}


# ---------------------------------------------------------------------------
# ChainCallRecord — one entry in the call trace
# ---------------------------------------------------------------------------


class ChainCallRecord(BaseModel):
    """Audit record for a single bridged tool call within a chain.

    Returned in ``ChainRunResult.call_trace`` on every outcome, including
    crash and timeout, so that the model knows which tools already ran
    (e.g. the email of step 2 was already sent) and can avoid duplicate
    side-effects on retry.
    """

    tool: str
    args_digest: str
    status: Literal["ok", "error", "denied", "timeout"]
    duration_ms: int

    model_config = {"frozen": True}


# ---------------------------------------------------------------------------
# ChainRunResult — final outcome of a chain execution
# ---------------------------------------------------------------------------


class ChainRunResult(BaseModel):
    """Final outcome of a single ``ToolChainTool.execute()`` call.

    ``status``            — ``"ok"`` | ``"error"`` | ``"timeout"``
    ``output_text``       — plain-text captured from the chain's return value
    ``output_blocks_refs`` — ``ArtifactStore`` refs of returned media blocks
    ``logs``              — sandbox stdout/stderr
    ``tool_calls``        — total bridged tool calls made
    ``duration_ms``       — wall-clock time for the entire chain
    ``error``             — exception message / traceback excerpt on failure
    ``call_trace``        — ordered list of ``ChainCallRecord`` for every tool
                           call that ran, even on crash/timeout
    """

    status: Literal["ok", "error", "timeout"]
    output_text: str = ""
    output_blocks_refs: list[str] = Field(default_factory=list)
    logs: str = ""
    tool_calls: int = 0
    duration_ms: int = 0
    error: str | None = None
    call_trace: list[ChainCallRecord] = Field(default_factory=list)

    model_config = {"frozen": True}


__all__ = [
    "ChainPolicy",
    "ChainFile",
    "InvocationResult",
    "ChainCallRecord",
    "ChainRunResult",
]
