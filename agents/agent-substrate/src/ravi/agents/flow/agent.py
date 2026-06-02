"""Multi-agent flows — composable, deterministic execution pipelines.

Flows wrap one or more agents (or nested flows) and coordinate execution.
Every flow exposes the same ``run`` / ``run_stream`` surface as
``ReActAgent`` so flows can be nested or substituted wherever an agent is
expected.

Built-in flow types
-------------------
SequentialFlow
    Executes steps in order; each step receives the accumulated output of all
    previous steps appended to the original input.

ParallelFlow
    Runs all branches concurrently with ``asyncio.gather``; outputs merged
    via a configurable strategy (concat / vote / custom callable).

ConditionalFlow
    Evaluates a predicate against the current input and routes to one of two
    sub-flows (``if_true`` / ``if_false``).
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, Callable, List, Optional, Union
from uuid import uuid4

from ravi.kernel.stream import TextDelta
from ravi.agents.core.agent import AgentRunResult, ReActAgent
from ravi.agents.hooks.manager import HookEvent, HookManager

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

# A "step" is either a concrete ReActAgent or a nested flow.
FlowStep = Union[ReActAgent, "BaseFlow"]

MergeStrategy = Union[
    str,  # "concat" | "vote"
    Callable[[List[str]], str],
]


# ---------------------------------------------------------------------------
# BaseFlow
# ---------------------------------------------------------------------------


class BaseFlow(ABC):
    """Abstract base for all multi-agent flows.

    Parameters
    ----------
    name:        Unique identifier used in graphs and SSE events.
    description: Human-readable purpose.
    hooks:       Optional HookManager to receive FLOW_START / FLOW_END events.
    """

    def __init__(
        self,
        name: str,
        description: str = "",
        *,
        hooks: Optional[HookManager] = None,
    ) -> None:
        self.name = name
        self.description = description
        self.hooks = hooks or HookManager()

    @abstractmethod
    async def run(self, input_text: str, **kwargs: Any) -> AgentRunResult:
        """Execute the flow to completion."""
        ...

    @abstractmethod
    async def run_stream(self, input_text: str, **kwargs: Any) -> AsyncIterator[Any]:
        """Execute the flow, yielding stream events tagged with ``agent_id``."""
        ...

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}(name={self.name!r})>"


# ---------------------------------------------------------------------------
# SequentialFlow
# ---------------------------------------------------------------------------


class SequentialFlow(BaseFlow):
    """Execute steps in order, piping accumulated output → next step input.

    Parameters
    ----------
    steps:       Ordered list of agents / nested flows.
    name:        Flow identifier.
    description: Human-readable purpose.
    hooks:       Optional hook manager for FLOW_* events.
    """

    def __init__(
        self,
        steps: List[FlowStep],
        name: str = "sequential_flow",
        description: str = "Sequential multi-agent pipeline",
        *,
        hooks: Optional[HookManager] = None,
    ) -> None:
        super().__init__(name=name, description=description, hooks=hooks)
        if not steps:
            raise ValueError("SequentialFlow requires at least one step")
        self.steps = steps

    async def run(self, input_text: str, **kwargs: Any) -> AgentRunResult:
        run_id = str(uuid4())
        await self.hooks.dispatch(
            HookEvent.FLOW_START,
            {"flow": self.name, "run_id": run_id, "steps": len(self.steps)},
        )

        accumulated = input_text
        last_result: AgentRunResult | None = None

        for step in self.steps:
            last_result = await step.run(accumulated, **kwargs)
            if last_result.output:
                accumulated = f"{accumulated}\n\n{last_result.output}"

        await self.hooks.dispatch(
            HookEvent.FLOW_END,
            {
                "flow": self.name,
                "run_id": run_id,
                "status": last_result.status if last_result else "error",
            },
        )

        if last_result is None:
            return AgentRunResult(output="", status="error", run_id=run_id)
        return AgentRunResult(
            output=last_result.output,
            status=last_result.status,
            tool_calls=last_result.tool_calls,
            run_id=run_id,
        )

    async def run_stream(self, input_text: str, **kwargs: Any) -> AsyncIterator[Any]:
        run_id = str(uuid4())
        await self.hooks.dispatch(
            HookEvent.FLOW_START, {"flow": self.name, "run_id": run_id}
        )

        accumulated = input_text
        for step in self.steps:
            agent_id = step.name
            partial: list[str] = []
            async for chunk in step.run_stream(accumulated, **kwargs):
                if hasattr(chunk, "__dict__"):
                    vars(chunk)["agent_id"] = agent_id
                yield chunk
                if isinstance(chunk, TextDelta):
                    partial.append(chunk.text)
            if partial:
                accumulated = f"{accumulated}\n\n{''.join(partial)}"

        await self.hooks.dispatch(
            HookEvent.FLOW_END, {"flow": self.name, "run_id": run_id}
        )


# ---------------------------------------------------------------------------
# ParallelFlow
# ---------------------------------------------------------------------------


class ParallelFlow(BaseFlow):
    """Run all branches concurrently and merge outputs.

    Merge strategies
    ----------------
    ``"concat"`` (default)  — join outputs with ``\\n\\n`` in branch order.
    ``"vote"``              — majority vote; ties broken by branch order.
    ``Callable``            — custom ``(outputs: list[str]) -> str``.

    Parameters
    ----------
    branches:    List of agents / flows to run in parallel.
    name:        Flow identifier.
    description: Human-readable purpose.
    merge:       Merge strategy (default ``"concat"``).
    hooks:       Optional hook manager.
    """

    def __init__(
        self,
        branches: List[FlowStep],
        name: str = "parallel_flow",
        description: str = "Parallel multi-agent execution",
        *,
        merge: MergeStrategy = "concat",
        hooks: Optional[HookManager] = None,
    ) -> None:
        super().__init__(name=name, description=description, hooks=hooks)
        if not branches:
            raise ValueError("ParallelFlow requires at least one branch")
        self.branches = branches
        self.merge = merge

    def _merge_outputs(self, outputs: List[str]) -> str:
        if callable(self.merge):
            return self.merge(outputs)
        if self.merge == "vote":
            from collections import Counter

            return Counter(outputs).most_common(1)[0][0]
        return "\n\n".join(outputs)

    async def run(self, input_text: str, **kwargs: Any) -> AgentRunResult:
        run_id = str(uuid4())
        await self.hooks.dispatch(
            HookEvent.FLOW_START,
            {"flow": self.name, "run_id": run_id, "branches": len(self.branches)},
        )

        results: list[AgentRunResult] = await asyncio.gather(
            *[step.run(input_text, **kwargs) for step in self.branches]
        )

        merged = self._merge_outputs([r.output for r in results])
        all_tool_calls = [tc for r in results for tc in r.tool_calls]
        status = "success" if all(r.status == "success" for r in results) else "error"

        await self.hooks.dispatch(
            HookEvent.FLOW_END, {"flow": self.name, "run_id": run_id}
        )
        return AgentRunResult(
            output=merged, status=status, tool_calls=all_tool_calls, run_id=run_id
        )

    async def run_stream(self, input_text: str, **kwargs: Any) -> AsyncIterator[Any]:
        run_id = str(uuid4())
        await self.hooks.dispatch(
            HookEvent.FLOW_START, {"flow": self.name, "run_id": run_id}
        )

        queue: asyncio.Queue[Any | None] = asyncio.Queue()

        async def _drain(step: FlowStep) -> None:
            agent_id = step.name
            try:
                async for chunk in step.run_stream(input_text, **kwargs):
                    if hasattr(chunk, "__dict__"):
                        vars(chunk)["agent_id"] = agent_id
                    await queue.put(chunk)
            finally:
                await queue.put(None)

        tasks = [asyncio.create_task(_drain(s)) for s in self.branches]
        done = 0
        while done < len(self.branches):
            item = await queue.get()
            if item is None:
                done += 1
            else:
                yield item

        await asyncio.gather(*tasks, return_exceptions=True)
        await self.hooks.dispatch(
            HookEvent.FLOW_END, {"flow": self.name, "run_id": run_id}
        )


# ---------------------------------------------------------------------------
# ConditionalFlow
# ---------------------------------------------------------------------------


class ConditionalFlow(BaseFlow):
    """Route to one of two sub-flows based on a predicate.

    Parameters
    ----------
    predicate:   ``(input_text: str) -> bool`` — called at runtime.
    if_true:     Branch taken when predicate is truthy.
    if_false:    Branch taken when predicate is falsy.
    name:        Flow identifier.
    description: Human-readable purpose.
    hooks:       Optional hook manager.
    """

    def __init__(
        self,
        predicate: Callable[[str], bool],
        if_true: FlowStep,
        if_false: FlowStep,
        name: str = "conditional_flow",
        description: str = "Conditional branching flow",
        *,
        hooks: Optional[HookManager] = None,
    ) -> None:
        super().__init__(name=name, description=description, hooks=hooks)
        self.predicate = predicate
        self.if_true = if_true
        self.if_false = if_false

    def _select(self, input_text: str) -> FlowStep:
        try:
            return self.if_true if self.predicate(input_text) else self.if_false
        except Exception as exc:
            logger.warning("[%s] predicate raised %s — taking if_false", self.name, exc)
            return self.if_false

    async def run(self, input_text: str, **kwargs: Any) -> AgentRunResult:
        run_id = str(uuid4())
        branch = self._select(input_text)
        await self.hooks.dispatch(
            HookEvent.FLOW_START,
            {"flow": self.name, "run_id": run_id, "branch": branch.name},
        )
        result = await branch.run(input_text, **kwargs)
        await self.hooks.dispatch(
            HookEvent.FLOW_END,
            {"flow": self.name, "run_id": run_id, "branch": branch.name},
        )
        return result

    async def run_stream(self, input_text: str, **kwargs: Any) -> AsyncIterator[Any]:
        run_id = str(uuid4())
        branch = self._select(input_text)
        await self.hooks.dispatch(
            HookEvent.FLOW_START,
            {"flow": self.name, "run_id": run_id, "branch": branch.name},
        )
        async for chunk in branch.run_stream(input_text, **kwargs):
            if hasattr(chunk, "__dict__"):
                vars(chunk)["agent_id"] = branch.name
            yield chunk
        await self.hooks.dispatch(
            HookEvent.FLOW_END,
            {"flow": self.name, "run_id": run_id, "branch": branch.name},
        )
