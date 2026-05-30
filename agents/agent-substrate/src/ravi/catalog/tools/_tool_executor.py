"""ToolExecutorHandler — executes tool calls dispatched via the agent runtime.

A ``MessageHandler`` that receives tool execution requests as messages
and dispatches to the appropriate tool.  This decouples tool execution
from the ReAct agent loop, enabling distributed and durable execution
backends.

Payload schema (incoming)::

    {
        "tool_name": str,
        "arguments": dict,
        "call_id": str,
    }

Response schema (outgoing)::

    {
        "content": list,          # ToolResult content blocks
        "is_error": bool,
        "app_data": dict | None,  # Optional MCP App data
        "call_id": str,
        "tool_name": str,
    }
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from ravi.logger import setup_logging
from ravi.kernel.runtime import MessageContext
from ravi.kernel.tools.approval import tool_needs_approval
from ravi.kernel.tools.parsing import find_tool, parse_runtime_tool_payload
from ravi.kernel.tools.base_tool import BaseTool, HitlMode, ToolResult
from ravi.catalog.tools.human_input.tool import (
    ToolApprovalHandler,
    ToolApprovalRequest,
    ToolApprovalResponse,
    ToolApprovalAction,
)

logger = setup_logging()


class ToolExecutorHandler:
    """Stateless handler that executes tool calls routed through the runtime.

    The handler is registered on the runtime as agent type ``"tool_executor"``
    with a key matching the thread/session ID.  When ``ReActAgent._execute_tool()``
    dispatches via the runtime, this handler receives the payload and returns
    the result.

    Args:
        tools: Mapping of tool name → ``BaseTool`` instance.
        tool_timeout: Maximum seconds to wait for tool execution.
        tool_approval_handler: Optional HITL handler for tool approval gates.
        tools_requiring_approval: List of tool names that need approval.
    """

    def __init__(
        self,
        *,
        tools: Dict[str, BaseTool],
        tool_timeout: Optional[float] = None,
        tool_approval_handler: Optional[ToolApprovalHandler] = None,
        tools_requiring_approval: Optional[List[str]] = None,
    ) -> None:
        self._tools = tools
        self._tool_timeout = tool_timeout
        self._approval_handler = tool_approval_handler
        self._tools_requiring_approval = (
            None if tools_requiring_approval is None else set(tools_requiring_approval)
        )

    async def __call__(self, ctx: MessageContext, payload: Any) -> Any:
        """Execute a tool call and return the result dict.

        Validates the payload, looks up the tool, optionally runs HITL
        approval, then executes with timeout.
        """
        try:
            parsed = parse_runtime_tool_payload(payload)
        except ValueError:
            return self._error_response(
                call_id="unknown",
                tool_name="unknown",
                error="Invalid payload: expected dict",
            )

        tool_name = parsed.name
        arguments = parsed.arguments
        call_id = parsed.call_id

        # Look up tool
        tool = find_tool(tool_name, self._tools)
        if tool is None:
            return self._error_response(
                call_id=call_id,
                tool_name=tool_name,
                error=f"Tool '{tool_name}' not found",
            )

        # HITL approval gate
        if self._approval_handler and tool_needs_approval(
            tool_name, self._tools_requiring_approval
        ):
            hitl_mode: HitlMode = getattr(tool, "hitl_mode", HitlMode.BLOCKING)
            approval_request = ToolApprovalRequest(
                tool_name=tool_name,
                call_id=call_id,
                arguments=arguments,
                context=f"Runtime tool executor: '{tool_name}'",
                hitl_mode=hitl_mode.value
                if hasattr(hitl_mode, "value")
                else str(hitl_mode),
                hitl_timeout_seconds=getattr(tool, "hitl_timeout_seconds", None),
            )
            try:
                approval: ToolApprovalResponse = (
                    await self._approval_handler.request_approval(approval_request)
                )
            except Exception as exc:
                logger.error("Approval handler error for %s: %s", tool_name, exc)
                approval = ToolApprovalResponse(
                    request_id=approval_request.request_id,
                    action=ToolApprovalAction.DENY,
                    reason=f"Approval handler error: {exc}",
                )

            if approval.action == ToolApprovalAction.DENY:
                return self._error_response(
                    call_id=call_id,
                    tool_name=tool_name,
                    error=f"Tool denied by user: {approval.reason or 'no reason'}",
                )

            if approval.action == ToolApprovalAction.MODIFY:
                if approval.modified_arguments:
                    arguments = approval.modified_arguments

        # Execute with timeout
        try:
            if self._tool_timeout:
                exec_result: ToolResult = await asyncio.wait_for(
                    tool.execute(**arguments),
                    timeout=self._tool_timeout,
                )
            else:
                exec_result = await tool.execute(**arguments)

            return {
                "content": exec_result.content,
                "is_error": False,
                "app_data": exec_result.app_data,
                "call_id": call_id,
                "tool_name": tool_name,
            }

        except asyncio.TimeoutError:
            return self._error_response(
                call_id=call_id,
                tool_name=tool_name,
                error=f"Tool '{tool_name}' timed out after {self._tool_timeout}s",
            )
        except Exception as exc:
            return self._error_response(
                call_id=call_id,
                tool_name=tool_name,
                error=str(exc),
            )

    @staticmethod
    def _error_response(*, call_id: str, tool_name: str, error: str) -> Dict[str, Any]:
        """Build a standardised error response dict."""
        return {
            "content": [{"type": "text", "text": error}],
            "is_error": True,
            "app_data": None,
            "call_id": call_id,
            "tool_name": tool_name,
        }
