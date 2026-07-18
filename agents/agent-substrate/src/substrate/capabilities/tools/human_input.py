"""Human-in-the-Loop (HITL) — Ask-Human pattern: the LLM decides to ask a
question with options.

For the other HITL pattern (Tool-Approval — a tool requiring human sign-off
before execution), see ``tool_approval.py``.

Architecture:
  - HumanInputRequest  — what the agent asks (question + options)
  - HumanInputResponse — what the user answers (choice or free text)
  - HumanInputHandler  — abstract callback (CLI, web UI, API, etc.)
  - AskHumanTool       — MCP-compatible tool the LLM calls to pause & ask
  - CLIHumanHandler    — built-in terminal-based implementation

Usage::

    from substrate.capabilities.tools.human_input import CLIHumanHandler, AskHumanTool

    handler = CLIHumanHandler()
    ask_tool = AskHumanTool(handler=handler)

    agent = ReActAgent(
        name="assistant",
        model_client=client,
        tools=[ask_tool, ...other_tools...],
    )
"""

from __future__ import annotations
from substrate.logger import setup_logging

import asyncio
import json
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, List
from uuid import uuid4

if TYPE_CHECKING:
    from substrate.agents.runtime.context import RunContext

from pydantic import BaseModel, Field

from substrate.kernel.tools import ToolExecutionResult
from substrate.kernel import TextBlock

logger = setup_logging()


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
    selected_key: str | None = None
    selected_label: str = ""
    freeform_text: str | None = None
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
                elif request.allow_freeform and choice_num == len(request.options) + 1:
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
                    valid_range = len(request.options) + (
                        1 if request.allow_freeform else 0
                    )
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


class AskHumanTool:
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

    risk: str = "safe"  # ask_human IS the human — never needs separate approval

    # The tool suspends the run waiting on a human; it must NOT be subject to the
    # ToolInvoker's per-call timeout (a human may take minutes to answer). The
    # human-wait timeout, if any, is the handler's / bridge's concern.
    suspends: bool = True

    description: str = (
        "Ask the user ONE question when you need their input, preference, "
        "or confirmation. Present 2-3 clear options plus an open-ended "
        "option for the user to type their own answer. Use this when you "
        "are unsure about the user's intent, need to choose between "
        "approaches, or want confirmation before taking an action.\n\n"
        "CRITICAL — ask one thing at a time, never a multi-field checklist:\n"
        "- Each option must be a complete, concrete answer the user could "
        "actually pick as-is (e.g. 'Dine-in, mid-range, Italian, Indiranagar, "
        "date night' or 'Delivery, budget-friendly'), never a template or a "
        "list of field names/placeholders like 'budget • food type • area'.\n"
        "- If you genuinely need several independent pieces of information "
        "(e.g. budget AND cuisine AND location), do NOT cram them into one "
        "question with templated options. Either: (a) ask a single focused "
        "question per call and make several calls in sequence, or (b) ask one "
        "open question and rely on the free-text answer — never invent "
        "options that just restate the field names you want filled in."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "The single, specific question to ask the user — not a bundle of several questions",
            },
            "context": {
                "type": "string",
                "description": "Brief context explaining why you need input",
            },
            "option_1": {
                "type": "string",
                "description": (
                    "First option — a complete, concrete answer the user could pick "
                    "as-is. Never a placeholder or field name (e.g. write "
                    "'Mid-range, ₹1000-2000 for two', not 'budget')."
                ),
            },
            "option_2": {
                "type": "string",
                "description": (
                    "Second option — a complete, concrete answer, same rule as option_1 (required)."
                ),
            },
            "option_3": {
                "type": "string",
                "description": (
                    "Third option — a complete, concrete answer, same rule as option_1 "
                    "(optional, leave empty to skip)."
                ),
            },
        },
        "required": ["question", "context", "option_1", "option_2"],
    }

    def __init__(
        self,
        handler: HumanInputHandler,
        *,
        name: str = "ask_human",
        max_requests_per_run: int = 3,
    ) -> None:
        self.name = name
        self.handler = handler
        self._request_count = 0
        self._max_requests = max_requests_per_run
        self._history: List[Dict[str, Any]] = []

    async def execute(
        self,
        *,
        ctx: RunContext | None = None,
        question: str,
        context: str,
        option_1: str,
        option_2: str,
        option_3: str = "",
        **_: Any,
    ) -> ToolExecutionResult:
        """Execute the human input request."""

        # Guard: limit requests per run
        if self._request_count >= self._max_requests:
            return ToolExecutionResult(
                content=[
                    TextBlock(
                        text=json.dumps(
                            {
                                "error": (
                                    f"Maximum human input requests reached "
                                    f"({self._max_requests}). Make your best "
                                    f"judgement and proceed."
                                ),
                            }
                        )
                    )
                ],
                is_error=True,
            )

        # Build options
        options: List[InputOption] = []
        for i, label in enumerate([option_1, option_2, option_3], 1):
            if label and label.strip():
                options.append(
                    InputOption(
                        key=str(i),
                        label=label.strip(),
                    )
                )

        if len(options) < 2:
            return ToolExecutionResult(
                content=[
                    TextBlock(
                        text=json.dumps(
                            {
                                "error": "Please provide at least 2 options.",
                            }
                        )
                    )
                ],
                is_error=True,
            )

        # Create request
        request = HumanInputRequest(
            question=question,
            context=context,
            options=options,
            allow_freeform=True,
        )

        logger.info(f"Human input requested: {question} ({len(options)} options)")

        # ── Signal-based (dead suspend) path ──────────────────────────────────
        # When the handler opts in to signal mode, we log `input.requested` and
        # suspend via SignalBusProtocol.  Zero compute is consumed while the human
        # decides.  The signal payload carries the user's action and is mapped
        # to a ToolExecutionResult by _shape_result().
        if ctx is not None and getattr(self.handler, "suspends_via_signal", False):
            # Replay-stable id: this call suspends via ctx.sleep_until_signal
            # below, which can raise SuspendInterrupt and unwind. On replay,
            # this tool body re-executes from the top (a suspending tool call
            # is never itself a journal hit while suspended — see RunContext
            # docstring) — a fresh uuid4() here would mint a NEW request_id
            # each attempt, orphaning whatever card the user is looking at.
            # ctx.uuid() is journaled, so every replay gets the SAME id.
            request.request_id = await ctx.uuid()
            log_payload = {
                "request_id": request.request_id,
                "question": request.question,
                "context": request.context,
                "options": [
                    {"key": o.key, "label": o.label, "description": o.description}
                    for o in options
                ],
                "allow_freeform": request.allow_freeform,
                "run_id": ctx.run_id,
            }
            try:
                # log_once, not _log: this whole tool body re-executes on
                # every resume (the outer tool() wrapper's effect can never
                # be recorded before a suspend — see RunContext.log_once's
                # docstring), so a plain _log here would append a duplicate
                # input.requested entry — and a duplicate question card in
                # the UI — every time this run suspends and resumes.
                await ctx.log_once("input.requested", log_payload)
            except Exception:
                pass
            signal_payload = await ctx.sleep_until_signal(f"hitl:{request.request_id}")
            self._request_count += 1
            return self._shape_result(request, signal_payload)

        # ── Event-log path (non-signal handlers that opt in to log emission) ──
        # Emit `input.requested` so the console stream_adapter can render the
        # picker card while handler.request_input() blocks in the executor.
        if ctx is not None and getattr(self.handler, "supports_event_log", False):
            try:
                await ctx._log(
                    "input.requested",
                    {
                        "request_id": request.request_id,
                        "question": request.question,
                        "context": request.context,
                        "options": [
                            {
                                "key": o.key,
                                "label": o.label,
                                "description": o.description,
                            }
                            for o in options
                        ],
                        "allow_freeform": request.allow_freeform,
                        "run_id": ctx.run_id,
                    },
                )
            except Exception:
                pass  # never let logging break the tool

        # Guard: handler must be wired (placeholder instances have handler=None)
        if self.handler is None:
            logger.error(
                "AskHumanTool.handler is None — tool was not wired to a bridge"
            )
            return ToolExecutionResult(
                content=[
                    TextBlock(
                        text=json.dumps(
                            {
                                "error": "Human input handler not configured for this session",
                            }
                        )
                    )
                ],
                is_error=True,
            )

        # Collect response (blocking — runs in executor for CLI, Future for web)
        try:
            response = await self.handler.request_input(request)
        except Exception as e:
            logger.error(f"Human input handler error: {e}")
            return ToolExecutionResult(
                content=[
                    TextBlock(
                        text=json.dumps(
                            {
                                "error": f"Failed to get human input: {e}",
                            }
                        )
                    )
                ],
                is_error=True,
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
            return ToolExecutionResult(
                content=[
                    TextBlock(
                        text=json.dumps(
                            {
                                "status": "timed_out",
                                "message": (
                                    "User did not respond in time. "
                                    "Proceed with your best judgement."
                                ),
                            }
                        )
                    )
                ],
                is_error=False,
            )

        # Build result
        result_data = {
            "status": "answered",
            "user_choice": response.answer,
            "was_freeform": response.is_freeform,
            "selected_option": response.selected_label
            if not response.is_freeform
            else None,
        }

        return ToolExecutionResult(
            content=[TextBlock(text=json.dumps(result_data))],
            is_error=False,
        )

    def _shape_result(
        self, request: HumanInputRequest, payload: dict
    ) -> ToolExecutionResult:
        """Map a signal payload ``{action, ...}`` to a ToolExecutionResult.

        Every path returns a valid result so no ``tool_use`` is ever left
        without a ``tool_result`` in the message history.

        The result JSON also echoes the question + options under ``_card`` so a
        UI can rebuild the answered card on reload purely from the (reliably
        persisted) tool_result — the assistant turn's tool_calls are not a
        dependable source (turn-flush timing can drop ask-only turns).
        """
        action = payload.get("action", "answered")
        card = {
            "request_id": request.request_id,
            "question": request.question,
            "context": request.context,
            "options": [
                {"key": o.key, "label": o.label, "description": o.description}
                for o in request.options
            ],
            "allow_freeform": request.allow_freeform,
        }

        if action == "skipped":
            self._history.append(
                {
                    "request_id": request.request_id,
                    "question": request.question,
                    "options": [o.label for o in request.options],
                    "answer": "(skipped)",
                    "is_freeform": False,
                    "timed_out": True,
                }
            )
            return ToolExecutionResult(
                content=[
                    TextBlock(
                        text=json.dumps(
                            {
                                "status": "timed_out",
                                "message": (
                                    "User did not respond in time. "
                                    "Proceed with your best judgement."
                                ),
                                "_card": card,
                            }
                        )
                    )
                ],
                is_error=False,
            )

        if action == "cancelled":
            self._history.append(
                {
                    "request_id": request.request_id,
                    "question": request.question,
                    "options": [o.label for o in request.options],
                    "answer": "(cancelled)",
                    "is_freeform": False,
                    "timed_out": False,
                }
            )
            return ToolExecutionResult(
                content=[
                    TextBlock(
                        text=json.dumps(
                            {
                                "status": "cancelled",
                                "message": "User moved on without answering.",
                                "_card": card,
                            }
                        )
                    )
                ],
                is_error=False,
            )

        # action == "answered"
        freeform = payload.get("freeform_text")
        selected_key = payload.get("selected_key")
        selected_label = payload.get("selected_label", "")
        if not selected_label and selected_key:
            opt = next((o for o in request.options if o.key == selected_key), None)
            if opt:
                selected_label = opt.label
        user_choice = freeform if freeform else selected_label
        is_freeform = bool(freeform)

        self._history.append(
            {
                "request_id": request.request_id,
                "question": request.question,
                "options": [o.label for o in request.options],
                "answer": user_choice,
                "is_freeform": is_freeform,
                "timed_out": False,
            }
        )
        return ToolExecutionResult(
            content=[
                TextBlock(
                    text=json.dumps(
                        {
                            "status": "answered",
                            "user_choice": user_choice,
                            "was_freeform": is_freeform,
                            "selected_option": selected_label
                            if not is_freeform
                            else None,
                            "_card": card,
                        }
                    )
                )
            ],
            is_error=False,
        )

    def reset(self) -> None:
        """Reset request counter (called between agent runs)."""
        self._request_count = 0
        self._history.clear()

    @property
    def interaction_history(self) -> List[Dict[str, Any]]:
        """Return all human interactions from this run."""
        return list(self._history)


__all__ = [
    "InputOption",
    "HumanInputRequest",
    "HumanInputResponse",
    "HumanInputHandler",
    "CLIHumanHandler",
    "CallbackHumanHandler",
    "AskHumanTool",
]
