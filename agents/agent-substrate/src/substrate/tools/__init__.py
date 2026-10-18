"""substrate.tools — Tools: the Tool contracts, approval, chaining policy, skills, and the Toolbox."""

from __future__ import annotations

from substrate.tools.approval import (
    ApprovalDecision,
    ApprovalHandler,
    ApprovalRequest,
    ApprovalResult,
    AutoApprove,
    DurableApproval,
    approval_signal,
)
from substrate.tools.chain import (
    ChainCallRecord,
    ChainFile,
    ChainPolicy,
    ChainRunResult,
    InvocationResult,
)
from substrate.tools.function import FunctionTool, tool
from substrate.tools.protocols import (
    AnyTool,
    HostedTool,
    PayloadBase,
    ProviderDefinedTool,
    Tool,
    ToolCallRequest,
    ToolExecutionResult,
    ToolRegistry,
    ToolRisk,
    ToolType,
    ToolUI,
    is_concurrency_safe,
    is_hosted_tool,
    is_provider_defined_tool,
)
from substrate.tools.skills import (
    Skill,
)
from substrate.tools.toolbox import (
    Toolbox,
)

__all__ = [
    "AnyTool",
    "ApprovalDecision",
    "ApprovalHandler",
    "ApprovalRequest",
    "ApprovalResult",
    "AutoApprove",
    "ChainCallRecord",
    "ChainFile",
    "ChainPolicy",
    "ChainRunResult",
    "FunctionTool",
    "DurableApproval",
    "HostedTool",
    "InvocationResult",
    "PayloadBase",
    "ProviderDefinedTool",
    "Skill",
    "Tool",
    "ToolCallRequest",
    "ToolExecutionResult",
    "ToolRegistry",
    "ToolRisk",
    "ToolType",
    "ToolUI",
    "Toolbox",
    "approval_signal",
    "is_concurrency_safe",
    "is_hosted_tool",
    "is_provider_defined_tool",
    "tool",
]
