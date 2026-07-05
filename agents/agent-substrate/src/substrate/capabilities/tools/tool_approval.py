"""Human-in-the-Loop (HITL) tool approval — approve / deny / modify.

Split out of ``human_input.py`` (HITL Pattern 2 there — see that module's
docstring for the full two-pattern HITL overview). Certain tools require
human approval before execution; configured per-tool via
``tools_requiring_approval`` on the agent.

Architecture:
  - ToolApprovalRequest  — what tool wants to run (name + args)
  - ToolApprovalResponse — approve / deny / modify
  - ToolApprovalHandler  — abstract callback for approval
  - CLIApprovalHandler   — built-in terminal-based approval
  - CallbackApprovalHandler — delegates to an async callback (web/API)
"""

from __future__ import annotations

import asyncio
import json
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Awaitable, Callable, Dict
from uuid import uuid4

from pydantic import BaseModel, Field


class ToolApprovalAction(str, Enum):
    """Action the user takes on a tool-approval request."""

    APPROVE = "approve"
    DENY = "deny"
    MODIFY = "modify"


class ToolApprovalRequest(BaseModel):
    """A request for human approval before executing a tool.

    Sent to the user when the agent wants to call a tool that
    requires approval. The user can approve, deny, or modify the
    arguments.
    """

    request_id: str = Field(default_factory=lambda: str(uuid4()))
    tool_name: str
    call_id: str = ""
    arguments: Dict[str, Any] = Field(default_factory=dict)
    context: str = ""
    # HITL behaviour declared on the tool — read by WebHITLBridge
    hitl_mode: str = "blocking"  # HitlMode value
    hitl_timeout_seconds: float | None = None  # only used in continue_on_timeout


class ToolApprovalResponse(BaseModel):
    """The user's response to a tool-approval request.

    Attributes:
        request_id: Matches the request's ID.
        action: approve / deny / modify.
        modified_arguments: New arguments if action is MODIFY.
        reason: Optional explanation from the user.
    """

    request_id: str = ""
    action: ToolApprovalAction = ToolApprovalAction.APPROVE
    modified_arguments: Dict[str, Any] | None = None
    reason: str = ""


# ---------------------------------------------------------------------------
# Abstract approval handler
# ---------------------------------------------------------------------------


class ToolApprovalHandler(ABC):
    """Interface for collecting tool-execution approval from a human."""

    @abstractmethod
    async def request_approval(
        self, request: ToolApprovalRequest
    ) -> ToolApprovalResponse:
        """Present the approval request and wait for a response."""
        ...


# ---------------------------------------------------------------------------
# CLI approval handler (built-in)
# ---------------------------------------------------------------------------


class CLIApprovalHandler(ToolApprovalHandler):
    """Terminal-based tool-approval handler.

    Displays tool name + arguments, prompts for Approve / Deny / Modify.
    """

    async def request_approval(
        self, request: ToolApprovalRequest
    ) -> ToolApprovalResponse:
        return await asyncio.get_event_loop().run_in_executor(
            None, self._collect_approval_sync, request
        )

    def _collect_approval_sync(
        self, request: ToolApprovalRequest
    ) -> ToolApprovalResponse:
        print("\n" + "=" * 60)
        print("  TOOL APPROVAL REQUIRED")
        print("=" * 60)

        print(f"\n  Tool:  {request.tool_name}")
        if request.context:
            print(f"  Why:   {request.context}")

        print("\n  Arguments:")
        args_str = json.dumps(request.arguments, indent=4)
        for line in args_str.splitlines():
            print(f"    {line}")

        print()
        print("    [1] Approve — execute as-is")
        print("    [2] Deny    — block this call")
        print("    [3] Modify  — edit arguments, then approve")
        print()

        while True:
            try:
                choice = input("  Your choice (1/2/3): ").strip()

                if choice == "1":
                    print("\n  ✓ Approved")
                    print("=" * 60 + "\n")
                    return ToolApprovalResponse(
                        request_id=request.request_id,
                        action=ToolApprovalAction.APPROVE,
                    )

                elif choice == "2":
                    reason = input("  Reason (optional): ").strip()
                    print("\n  ✗ Denied")
                    print("=" * 60 + "\n")
                    return ToolApprovalResponse(
                        request_id=request.request_id,
                        action=ToolApprovalAction.DENY,
                        reason=reason,
                    )

                elif choice == "3":
                    print("  Enter modified arguments as JSON:")
                    raw = input("  > ").strip()
                    try:
                        new_args = json.loads(raw)
                        reason = input("  Reason (optional): ").strip()
                        print("\n  ⟳ Modified & approved")
                        print("=" * 60 + "\n")
                        return ToolApprovalResponse(
                            request_id=request.request_id,
                            action=ToolApprovalAction.MODIFY,
                            modified_arguments=new_args,
                            reason=reason,
                        )
                    except json.JSONDecodeError:
                        print("  Invalid JSON. Try again.")

                else:
                    print("  Please enter 1, 2, or 3.")

            except (EOFError, KeyboardInterrupt):
                print("\n  Input cancelled — denying by default.")
                return ToolApprovalResponse(
                    request_id=request.request_id,
                    action=ToolApprovalAction.DENY,
                    reason="User cancelled input",
                )


# ---------------------------------------------------------------------------
# Callback-based approval handler (for web/API integration)
# ---------------------------------------------------------------------------


class CallbackApprovalHandler(ToolApprovalHandler):
    """Approval handler that delegates to an async callback.

    Usage::

        async def my_approval_callback(req: ToolApprovalRequest) -> ToolApprovalResponse:
            # Send to frontend, wait for response
            ...

        handler = CallbackApprovalHandler(callback=my_approval_callback)
    """

    def __init__(
        self,
        callback: Callable[[ToolApprovalRequest], Awaitable[ToolApprovalResponse]],
    ):
        self._callback = callback

    async def request_approval(
        self, request: ToolApprovalRequest
    ) -> ToolApprovalResponse:
        return await self._callback(request)


__all__ = [
    "ToolApprovalAction",
    "ToolApprovalRequest",
    "ToolApprovalResponse",
    "ToolApprovalHandler",
    "CLIApprovalHandler",
    "CallbackApprovalHandler",
]
