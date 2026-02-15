"""Agent lifecycle hooks for extensibility.

Allows external code to observe and react to events in the agent
run loop without modifying core agent code.

Events:
  - on_run_start      — agent.run() begins
  - on_run_end        — agent.run() completes (success or failure)
  - on_step_start     — a new think-act cycle begins
  - on_step_end       — a think-act cycle completes
  - on_llm_start      — before calling the LLM
  - on_llm_end        — after LLM responds
  - on_tool_start     — before executing a tool
  - on_tool_end       — after tool execution completes
  - on_guardrail_trip — a guardrail triggered a hard stop

Design decisions:
  - Hooks are async to support I/O (logging to DB, sending webhooks).
  - Hooks receive read-only context dicts — they cannot mutate state.
  - Exceptions in hooks are caught and logged — they never crash the agent.
  - Hooks are registered per-agent, not globally, for isolation.
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger("agent_framework.hooks")


# ---------------------------------------------------------------------------
# Event types
# ---------------------------------------------------------------------------

class HookEvent(str, Enum):
    """Agent lifecycle events that can be hooked."""
    RUN_START = "on_run_start"
    RUN_END = "on_run_end"
    STEP_START = "on_step_start"
    STEP_END = "on_step_end"
    LLM_START = "on_llm_start"
    LLM_END = "on_llm_end"
    TOOL_START = "on_tool_start"
    TOOL_END = "on_tool_end"
    GUARDRAIL_TRIP = "on_guardrail_trip"


# Type alias for hook callbacks
HookCallback = Callable[[Dict[str, Any]], Awaitable[None]]


# ---------------------------------------------------------------------------
# Hook Manager
# ---------------------------------------------------------------------------

class HookManager:
    """Manages lifecycle hook registrations and dispatching.

    Usage::

        hooks = HookManager()

        @hooks.on(HookEvent.RUN_START)
        async def log_start(ctx):
            print(f"Agent {ctx['agent_name']} starting...")

        @hooks.on(HookEvent.TOOL_END)
        async def track_tool(ctx):
            await db.log_tool_call(ctx['tool_name'], ctx['duration_ms'])

        # Register on agent
        agent = ReActAgent(..., hooks=hooks)
    """

    def __init__(self):
        self._hooks: Dict[HookEvent, List[HookCallback]] = defaultdict(list)

    def on(self, event: HookEvent):
        """Decorator to register a hook for an event.

        Usage::

            @hooks.on(HookEvent.RUN_START)
            async def my_hook(ctx: dict):
                ...
        """
        def decorator(func: HookCallback) -> HookCallback:
            self._hooks[event].append(func)
            return func
        return decorator

    def register(self, event: HookEvent, callback: HookCallback) -> None:
        """Register a hook callback programmatically."""
        self._hooks[event].append(callback)

    def unregister(self, event: HookEvent, callback: HookCallback) -> bool:
        """Remove a specific callback. Returns True if found."""
        try:
            self._hooks[event].remove(callback)
            return True
        except ValueError:
            return False

    def clear(self, event: Optional[HookEvent] = None) -> None:
        """Clear hooks for a specific event or all events."""
        if event:
            self._hooks[event].clear()
        else:
            self._hooks.clear()

    async def dispatch(self, event: HookEvent, context: Dict[str, Any]) -> None:
        """Fire all hooks for an event.

        Hooks run in parallel. Exceptions are caught and logged
        to prevent hook failures from crashing the agent.
        """
        callbacks = self._hooks.get(event, [])
        if not callbacks:
            return

        async def _safe_call(cb: HookCallback) -> None:
            try:
                await cb(context)
            except Exception as e:
                logger.error(
                    f"Hook error in {cb.__name__} for {event.value}: {e}",
                    exc_info=True,
                )

        await asyncio.gather(*[_safe_call(cb) for cb in callbacks])

    def has_hooks(self, event: HookEvent) -> bool:
        """Check if any hooks are registered for an event."""
        return bool(self._hooks.get(event))

    @property
    def registered_events(self) -> List[HookEvent]:
        """Return events that have at least one hook registered."""
        return [e for e, hooks in self._hooks.items() if hooks]


# ---------------------------------------------------------------------------
# Pre-built hook implementations
# ---------------------------------------------------------------------------

class CostTracker:
    """Hook that tracks estimated LLM costs per run.

    Usage::

        tracker = CostTracker(cost_per_1k_prompt=0.01, cost_per_1k_completion=0.03)
        hooks = HookManager()
        hooks.register(HookEvent.LLM_END, tracker.on_llm_end)
        hooks.register(HookEvent.RUN_END, tracker.on_run_end)
    """

    # Default pricing (GPT-4o as of 2025)
    DEFAULT_PRICING = {
        "gpt-4o": {"prompt": 0.0025, "completion": 0.01},
        "gpt-4o-mini": {"prompt": 0.00015, "completion": 0.0006},
        "gpt-4.1": {"prompt": 0.002, "completion": 0.008},
        "gpt-4.1-mini": {"prompt": 0.0004, "completion": 0.0016},
        "gpt-4.1-nano": {"prompt": 0.0001, "completion": 0.0004},
    }

    def __init__(
        self,
        cost_per_1k_prompt: Optional[float] = None,
        cost_per_1k_completion: Optional[float] = None,
        model: Optional[str] = None,
    ):
        if model and model in self.DEFAULT_PRICING:
            pricing = self.DEFAULT_PRICING[model]
            self.cost_per_1k_prompt = cost_per_1k_prompt or pricing["prompt"]
            self.cost_per_1k_completion = cost_per_1k_completion or pricing["completion"]
        else:
            self.cost_per_1k_prompt = cost_per_1k_prompt or 0.0025
            self.cost_per_1k_completion = cost_per_1k_completion or 0.01

        self.total_cost = 0.0
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.call_count = 0

    async def on_llm_end(self, ctx: Dict[str, Any]) -> None:
        """Track cost after each LLM call."""
        usage = ctx.get("usage")
        if usage:
            prompt_tokens = getattr(usage, "prompt_tokens", 0)
            completion_tokens = getattr(usage, "completion_tokens", 0)

            cost = (
                (prompt_tokens / 1000) * self.cost_per_1k_prompt +
                (completion_tokens / 1000) * self.cost_per_1k_completion
            )
            self.total_cost += cost
            self.total_prompt_tokens += prompt_tokens
            self.total_completion_tokens += completion_tokens
            self.call_count += 1

    async def on_run_end(self, ctx: Dict[str, Any]) -> None:
        """Log total cost at end of run."""
        logger.info(
            f"Run cost: ${self.total_cost:.6f} "
            f"({self.total_prompt_tokens} prompt + "
            f"{self.total_completion_tokens} completion tokens, "
            f"{self.call_count} calls)"
        )

    def reset(self) -> None:
        self.total_cost = 0.0
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.call_count = 0

    @property
    def stats(self) -> Dict[str, Any]:
        return {
            "total_cost_usd": round(self.total_cost, 6),
            "total_prompt_tokens": self.total_prompt_tokens,
            "total_completion_tokens": self.total_completion_tokens,
            "call_count": self.call_count,
        }


class RunLogger:
    """Hook that logs all lifecycle events for debugging.

    Usage::

        run_logger = RunLogger()
        hooks = HookManager()
        for event in HookEvent:
            hooks.register(event, run_logger.log)
    """

    def __init__(self, level: int = logging.DEBUG):
        self.level = level
        self.events: List[Dict[str, Any]] = []

    async def log(self, ctx: Dict[str, Any]) -> None:
        event = ctx.get("event", "unknown")
        agent = ctx.get("agent_name", "unknown")
        logger.log(self.level, f"[HOOK] {event} | agent={agent} | {self._summarize(ctx)}")
        self.events.append(ctx)

    @staticmethod
    def _summarize(ctx: Dict[str, Any]) -> str:
        """Create a brief summary of context for logging."""
        parts = []
        for key in ("run_id", "step", "tool_name", "duration_ms", "status"):
            if key in ctx:
                parts.append(f"{key}={ctx[key]}")
        return ", ".join(parts) if parts else "no details"

    def clear(self) -> None:
        self.events.clear()
