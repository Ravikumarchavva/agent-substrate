"""Human-in-the-Loop (HITL) support.

Two HITL patterns are provided:

1. **Ask-Human** — the LLM decides to ask a question with options.
   - HumanInputRequest / HumanInputResponse / HumanInputHandler
   - AskHumanTool — MCP-compatible tool the LLM calls to pause & ask

2. **Tool-Approval** — certain tools require human approval before execution.
   - ToolApprovalRequest / ToolApprovalResponse / ToolApprovalHandler
   - Configured per-tool via ``tools_requiring_approval`` on the agent

Architecture:
  - HumanInputRequest  — what the agent asks (question + options)
  - HumanInputResponse — what the user answers (choice or free text)
  - HumanInputHandler  — abstract callback (CLI, web UI, API, etc.)
  - AskHumanTool       — MCP-compatible tool the LLM calls to pause & ask
  - CLIHumanHandler    — built-in terminal-based implementation
  - ToolApprovalRequest  — what tool wants to run (name + args)
  - ToolApprovalResponse — approve / deny / modify
  - ToolApprovalHandler  — abstract callback for approval
  - CLIApprovalHandler   — built-in terminal-based approval

Usage::

    from agent_framework.human_input import (
        CLIHumanHandler, AskHumanTool,
        CLIApprovalHandler,
    )

    handler = CLIHumanHandler()
    ask_tool = AskHumanTool(handler=handler)
    approval_handler = CLIApprovalHandler()

    agent = ReActAgent(
        name="assistant",
        model_client=client,
        tools=[ask_tool, ...other_tools...],
        tool_approval_handler=approval_handler,
        tools_requiring_approval=["dangerous_tool"],
    )
"""
from __future__ import annotations

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field

from agent_framework.tools.base_tool import BaseTool, Tool, ToolResult

logger = logging.getLogger("agent_framework.hitl")


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

class InputOption(BaseModel):
    """A single selectable option presented to the user.

    Attributes:
        key: Short identifier (e.g. "A", "1").
        label: Human-readable label displayed to the user.
        description: Optional longer explanation.
    """
    key: str
    label: str
    description: str = ""


class HumanInputRequest(BaseModel):
    """A request for human input.

    Contains the question, predefined options, and whether free text
    is allowed (always True by default — the "Other" option).

    Attributes:
        request_id: Unique ID for tracking.
        question: The question to ask the user.
        context: Why the agent is asking (shown to user).
        options: 2-4 predefined choices.
        allow_freeform: If True, user can type a custom answer.
        timeout_seconds: How long to wait before giving up (0 = forever).
    """
    request_id: str = Field(default_factory=lambda: str(uuid4()))
    question: str
    context: str = ""
    options: List[InputOption] = Field(default_factory=list)
    allow_freeform: bool = True
    timeout_seconds: float = 0.0  # 0 = no timeout


class HumanInputResponse(BaseModel):
    """The user's response to a HumanInputRequest.

    Attributes:
        request_id: Matches the request's ID.
        selected_key: Key of the selected option (None if freeform).
        selected_label: Label of the selected option.
        freeform_text: User's custom text (if they chose "Other").
        timed_out: True if the user didn't respond in time.
    """
    request_id: str = ""
    selected_key: Optional[str] = None
    selected_label: str = ""
    freeform_text: Optional[str] = None
    timed_out: bool = False

    @property
    def answer(self) -> str:
        """The effective answer — freeform text or selected label."""
        if self.freeform_text:
            return self.freeform_text
        return self.selected_label

    @property
    def is_freeform(self) -> bool:
        return self.freeform_text is not None and self.selected_key is None


# ---------------------------------------------------------------------------
# Abstract handler
# ---------------------------------------------------------------------------

class HumanInputHandler(ABC):
    """Interface for collecting human input.

    Implement this for your UI:
      - CLIHumanHandler  — terminal / stdin (built-in)
      - WebHumanHandler  — WebSocket / HTTP callback
      - SlackHandler     — Slack bot interaction
    """

    @abstractmethod
    async def request_input(self, request: HumanInputRequest) -> HumanInputResponse:
        """Present the request to a human and wait for their response.

        Args:
            request: The input request with question and options.

        Returns:
            HumanInputResponse with the user's choice.
        """
        ...


# ---------------------------------------------------------------------------
# CLI handler (built-in)
# ---------------------------------------------------------------------------

class CLIHumanHandler(HumanInputHandler):
    """Terminal-based human input handler.

    Displays options in the console and reads from stdin.
    Works in both sync and async contexts.
    """

    async def request_input(self, request: HumanInputRequest) -> HumanInputResponse:
        """Display question in terminal and collect user input."""
        # Run the blocking input() call in a thread to keep async happy
        return await asyncio.get_event_loop().run_in_executor(
            None, self._collect_input_sync, request
        )

    def _collect_input_sync(self, request: HumanInputRequest) -> HumanInputResponse:
        """Synchronous input collection (runs in executor)."""
        print("\n" + "=" * 60)
        print("  HUMAN INPUT REQUIRED")
        print("=" * 60)

        if request.context:
            print(f"\n  Context: {request.context}")

        print(f"\n  {request.question}\n")

        # Display numbered options
        for i, opt in enumerate(request.options, 1):
            desc = f" — {opt.description}" if opt.description else ""
            print(f"    [{i}] {opt.label}{desc}")

        # Free-form option
        if request.allow_freeform:
            freeform_num = len(request.options) + 1
            print(f"    [{freeform_num}] Other (type your own answer)")

        print()

        # Collect input
        while True:
            try:
                choice = input("  Your choice (number): ").strip()

                if not choice:
                    print("  Please enter a number.")
                    continue

                choice_num = int(choice)

                # Check if it's a valid option
                if 1 <= choice_num <= len(request.options):
                    selected = request.options[choice_num - 1]
                    print(f"\n  Selected: {selected.label}")
                    print("=" * 60 + "\n")
                    return HumanInputResponse(
                        request_id=request.request_id,
                        selected_key=selected.key,
                        selected_label=selected.label,
                    )

                # Free-form option
                elif (request.allow_freeform and
                      choice_num == len(request.options) + 1):
                    text = input("  Your answer: ").strip()
                    if not text:
                        print("  Please enter your answer.")
                        continue
                    print(f"\n  Your input: {text}")
                    print("=" * 60 + "\n")
                    return HumanInputResponse(
                        request_id=request.request_id,
                        freeform_text=text,
                    )

                else:
                    valid_range = len(request.options) + (1 if request.allow_freeform else 0)
                    print(f"  Please enter a number between 1 and {valid_range}.")

            except ValueError:
                print("  Please enter a valid number.")
            except (EOFError, KeyboardInterrupt):
                print("\n  Input cancelled.")
                return HumanInputResponse(
                    request_id=request.request_id,
                    timed_out=True,
                )


# ---------------------------------------------------------------------------
# Callback-based handler (for web/API integration)
# ---------------------------------------------------------------------------

class CallbackHumanHandler(HumanInputHandler):
    """Handler that delegates to an async callback function.

    Perfect for web UIs, WebSocket connections, Slack bots, etc.

    Usage::

        async def my_web_handler(request: HumanInputRequest) -> HumanInputResponse:
            # Send to WebSocket, wait for response
            await ws.send(request.model_dump_json())
            data = await ws.receive_json()
            return HumanInputResponse(**data)

        handler = CallbackHumanHandler(callback=my_web_handler)
    """

    def __init__(
        self,
        callback: Callable[[HumanInputRequest], Awaitable[HumanInputResponse]],
    ):
        self._callback = callback

    async def request_input(self, request: HumanInputRequest) -> HumanInputResponse:
        return await self._callback(request)


# ---------------------------------------------------------------------------
# AskHuman Tool — the LLM calls this to pause and ask
# ---------------------------------------------------------------------------

class AskHumanTool(BaseTool):
    """MCP-compatible tool that pauses execution to ask the user.

    The LLM calls this tool when it needs human guidance. It presents
    options and a free-text field, collects the response, and returns
    it to the LLM as a tool result.

    The LLM provides:
      - question: What to ask
      - context: Why it's asking
      - option_1, option_2, option_3: Predefined choices (2-3 required)

    Usage::

        handler = CLIHumanHandler()
        ask_tool = AskHumanTool(handler=handler)

        agent = ReActAgent(
            name="assistant",
            model_client=client,
            tools=[ask_tool],
        )
    """

    def __init__(
        self,
        handler: HumanInputHandler,
        *,
        name: str = "ask_human",
        max_requests_per_run: int = 3,
    ):
        self.handler = handler
        self._request_count = 0
        self._max_requests = max_requests_per_run
        self._history: List[Dict[str, Any]] = []

        super().__init__(
            name=name,
            description=(
                "Ask the user a question when you need their input, preference, "
                "or confirmation. Present 2-3 clear options plus an open-ended "
                "option for the user to type their own answer. Use this when you "
                "are unsure about the user's intent, need to choose between "
                "approaches, or want confirmation before taking an action."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "The question to ask the user",
                    },
                    "context": {
                        "type": "string",
                        "description": "Brief context explaining why you need input",
                    },
                    "option_1": {
                        "type": "string",
                        "description": "First option (required)",
                    },
                    "option_2": {
                        "type": "string",
                        "description": "Second option (required)",
                    },
                    "option_3": {
                        "type": "string",
                        "description": "Third option (optional, leave empty to skip)",
                    },
                },
                "required": ["question", "context", "option_1", "option_2"],
            },
        )

    async def execute(
        self,
        question: str,
        context: str = "",
        option_1: str = "",
        option_2: str = "",
        option_3: str = "",
        **kwargs,
    ) -> ToolResult:
        """Execute the human input request."""
        # Guard: limit requests per run
        if self._request_count >= self._max_requests:
            return ToolResult(
                content=[{
                    "type": "text",
                    "text": json.dumps({
                        "error": (
                            f"Maximum human input requests reached "
                            f"({self._max_requests}). Make your best "
                            f"judgement and proceed."
                        ),
                    }),
                }],
                isError=True,
            )

        # Build options
        options: List[InputOption] = []
        for i, label in enumerate([option_1, option_2, option_3], 1):
            if label and label.strip():
                options.append(InputOption(
                    key=str(i),
                    label=label.strip(),
                ))

        if len(options) < 2:
            return ToolResult(
                content=[{
                    "type": "text",
                    "text": json.dumps({
                        "error": "Please provide at least 2 options.",
                    }),
                }],
                isError=True,
            )

        # Create request
        request = HumanInputRequest(
            question=question,
            context=context,
            options=options,
            allow_freeform=True,
        )

        logger.info(
            f"Human input requested: {question} "
            f"({len(options)} options)"
        )

        # Collect response
        try:
            response = await self.handler.request_input(request)
        except Exception as e:
            logger.error(f"Human input handler error: {e}")
            return ToolResult(
                content=[{
                    "type": "text",
                    "text": json.dumps({
                        "error": f"Failed to get human input: {e}",
                    }),
                }],
                isError=True,
            )

        self._request_count += 1

        # Record in history
        record = {
            "request_id": request.request_id,
            "question": question,
            "options": [o.label for o in options],
            "answer": response.answer,
            "is_freeform": response.is_freeform,
            "timed_out": response.timed_out,
        }
        self._history.append(record)

        if response.timed_out:
            return ToolResult(
                content=[{
                    "type": "text",
                    "text": json.dumps({
                        "status": "timed_out",
                        "message": (
                            "User did not respond in time. "
                            "Proceed with your best judgement."
                        ),
                    }),
                }],
                isError=False,
            )

        # Build result
        result_data = {
            "status": "answered",
            "user_choice": response.answer,
            "was_freeform": response.is_freeform,
            "selected_option": response.selected_label if not response.is_freeform else None,
        }

        return ToolResult(
            content=[{
                "type": "text",
                "text": json.dumps(result_data),
            }],
            isError=False,
        )

    def reset(self) -> None:
        """Reset request counter (called between agent runs)."""
        self._request_count = 0
        self._history.clear()

    @property
    def interaction_history(self) -> List[Dict[str, Any]]:
        """Return all human interactions from this run."""
        return list(self._history)


# ═══════════════════════════════════════════════════════════════════════════
# HITL Pattern 2: Tool Approval (Approve / Deny / Modify)
# ═══════════════════════════════════════════════════════════════════════════


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
    timeout_seconds: float = 0.0


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
    modified_arguments: Optional[Dict[str, Any]] = None
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

        print(f"\n  Arguments:")
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
        callback: Callable[
            [ToolApprovalRequest], Awaitable[ToolApprovalResponse]
        ],
    ):
        self._callback = callback

    async def request_approval(
        self, request: ToolApprovalRequest
    ) -> ToolApprovalResponse:
        return await self._callback(request)