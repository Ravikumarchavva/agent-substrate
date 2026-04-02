"""Per-tool execution policies for durable workflows.

Maps tool names to ``ToolPolicy`` which governs:
- timeout, idempotency, HITL approval, large payload handling, retries.

Unknown tools get a policy derived from their ``BaseTool.risk`` and
``BaseTool.hitl_mode`` attributes via :func:`derive_policy_from_tool`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict


@dataclass(frozen=True)
class ToolPolicy:
    """Execution policy for a single tool inside a durable workflow.

    Attributes
    ----------
    timeout:
        Maximum seconds for tool execution (default 30).
    needs_idempotency:
        Whether ``ctx.uuid()`` should generate a stable idempotency key.
    requires_approval:
        Whether HITL approval is needed before execution.
    is_hitl_input:
        Whether this tool solicits human input (ask_human).
    large_payload:
        Whether results should use ``DataRef`` instead of inline journal.
    max_retries:
        Maximum retry attempts on transient failure.
    """

    timeout: float = 30.0
    needs_idempotency: bool = False
    requires_approval: bool = False
    is_hitl_input: bool = False
    large_payload: bool = False
    max_retries: int = 1


# ── Static policy table (known tools) ──────────────────────────────────

TOOL_POLICIES: Dict[str, ToolPolicy] = {
    "ask_human": ToolPolicy(
        is_hitl_input=True,
        timeout=300.0,
    ),
    "send_email": ToolPolicy(
        needs_idempotency=True,
        requires_approval=True,
        timeout=30.0,
    ),
    "manage_tasks": ToolPolicy(
        timeout=15.0,
    ),
    "web_surfer": ToolPolicy(
        timeout=120.0,
        max_retries=2,
    ),
    "code_interpreter": ToolPolicy(
        timeout=300.0,
    ),
    "file_manager": ToolPolicy(
        timeout=60.0,
    ),
    "calculator": ToolPolicy(
        timeout=10.0,
    ),
    "data_visualizer": ToolPolicy(
        timeout=30.0,
    ),
}


def derive_policy_from_tool(tool: Any) -> ToolPolicy:
    """Derive a :class:`ToolPolicy` from a ``BaseTool`` instance.

    Falls back to the static :data:`TOOL_POLICIES` table first.  If the
    tool is not in the table the policy is inferred from ``tool.risk``
    and ``tool.hitl_mode``.
    """
    name: str = getattr(tool, "name", "")
    if name in TOOL_POLICIES:
        return TOOL_POLICIES[name]

    # Lazy import to avoid pulling core into distributed at module level
    from raavan.core.tools.base_tool import HitlMode, ToolRisk

    risk = getattr(tool, "risk", ToolRisk.SAFE)
    hitl_mode = getattr(tool, "hitl_mode", HitlMode.BLOCKING)

    return ToolPolicy(
        timeout=getattr(tool, "hitl_timeout_seconds", None) or 30.0,
        needs_idempotency=(risk == ToolRisk.CRITICAL),
        requires_approval=(
            risk in (ToolRisk.CRITICAL, ToolRisk.SENSITIVE)
            and hitl_mode == HitlMode.BLOCKING
        ),
    )


def get_policy(tool_name: str) -> ToolPolicy:
    """Look up policy by name, returning a default if not found."""
    return TOOL_POLICIES.get(tool_name, ToolPolicy())
