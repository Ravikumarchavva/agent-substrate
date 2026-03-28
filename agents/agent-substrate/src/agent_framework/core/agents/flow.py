"""Multi-agent flows — composable, deterministic execution pipelines.

Flows wrap one or more agents (or nested flows) and coordinate how they
execute relative to each other.  Every flow exposes the same ``run`` /
``run_stream`` surface as a regular agent so flows can be nested or
substituted wherever an agent is expected.

Built-in flow types
-------------------
BaseFlow
    Abstract base.  Defines the ``run`` / ``run_stream`` / ``to_graph`` interface.

SequentialFlow
    Executes steps one after another.  The output of step N is appended to
    the input of step N+1.  Optionally shares memory across all steps.

ParallelFlow
    Runs all branches concurrently with ``asyncio.gather``.  Results are
    merged according to a configurable strategy (concat / vote / custom).

ConditionalFlow
    Evaluates a predicate against the current input and routes to one of two
    sub-flows (``if_true`` / ``if_false``).

All streaming variants tag every yielded chunk with an ``agent_id`` field so
the frontend can colour-code chunks per agent in the chat UI.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, Callable, List, Optional, Union
from uuid import uuid4

from agent_framework.core.agents.agent_result import (
    AgentRunResult,
    AggregatedUsage,
    RunStatus,
)
from agent_framework.core.agents.base_agent import BaseAgent
from agent_framework.core.agents.graph import FlowEdge, FlowGraph, FlowNode
from agent_framework.core.memory.memory_scope import MemoryScope
from agent_framework.core.hooks import HookEvent, HookManager
from agent_framework.shared.observability import logger


# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

# A "step" in a flow is either a concrete agent or a nested flow.
FlowStep = Union[BaseAgent, "BaseFlow"]

MergeStrategy = Union[
    str,  # "concat" | "vote"
    Callable[[List[str]], str],  # custom merge function
]


# ---------------------------------------------------------------------------
# BaseFlow
# ---------------------------------------------------------------------------


class BaseFlow(ABC):
    """Abstract base for all multi-agent flows.

    Flows mirror the ``BaseAgent`` interface for composability — you can
    pass a ``BaseFlow`` wherever a ``BaseAgent`` is accepted.

    Args:
        name:         Unique identifier used in graphs and SSE events.
        description:  Human-readable purpose description.
        hooks:        Optional ``HookManager`` to receive FLOW_START / FLOW_END
                      events on this flow's lifecycle.
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

    # -- Core interface -------------------------------------------------------

    @abstractmethod
    async def run(self, input_text: str, **kwargs) -> AgentRunResult:
        """Execute the flow to completion."""
        ...

    @abstractmethod
    async def run_stream(self, input_text: str, **kwargs) -> AsyncIterator[Any]:
        """Execute the flow, yielding chunks tagged with ``agent_id``."""
        ...

    @abstractmethod
    def to_graph(self) -> FlowGraph:
        """Return a ``FlowGraph`` describing this flow's topology."""
        ...

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}(name={self.name!r})>"


# ---------------------------------------------------------------------------
# SequentialFlow
# ---------------------------------------------------------------------------


class SequentialFlow(BaseFlow):
    """Execute steps one after another, piping output → input.

    Behaviour
    ---------
    * Each step receives the *combined* input: the original user message
      plus the outputs of all previous steps (separated by ``\\n\\n``).
    * When ``shared_memory_scope`` is ``MemoryScope.SHARED``, all agents that
      have ``memory_scope == MemoryScope.SHARED`` are given the *same*
      ``BaseMemory`` instance from the first SHARED agent encountered.
      Agents with ``ISOLATED`` or ``READ_ONLY_SHARED`` keep their own memory.
    * Streaming chunks are tagged with ``{"agent_id": agent.name}``.

    Args:
        steps:               Ordered list of agents / nested flows.
        name:                Flow identifier.
        description:         Human-readable purpose.
        shared_memory_scope: When ``SHARED``, agents with that scope share
                             one memory instance.  Defaults to ``ISOLATED``
                             (each agent keeps its own memory — safest).
        hooks:               Optional hook manager for FLOW_* events.
    """

    def __init__(
        self,
        steps: List[FlowStep],
        name: str = "sequential_flow",
        description: str = "Sequential multi-agent pipeline",
        *,
        shared_memory_scope: MemoryScope = MemoryScope.ISOLATED,
        hooks: Optional[HookManager] = None,
    ) -> None:
        super().__init__(name=name, description=description, hooks=hooks)
        if not steps:
            raise ValueError("SequentialFlow requires at least one step")
        self.steps = steps
        self.shared_memory_scope = shared_memory_scope
        self._bind_shared_memory()

    def _bind_shared_memory(self) -> None:
        """If shared scope is requested, wire agents to a single memory."""
        if self.shared_memory_scope != MemoryScope.SHARED:
            return
        shared_mem = None
        for step in self.steps:
            if isinstance(step, BaseAgent) and step.memory_scope == MemoryScope.SHARED:
                if shared_mem is None:
                    shared_mem = step.memory  # first SHARED agent owns the memory
                else:
                    step.memory = shared_mem  # subsequent ones borrow it

    async def run(self, input_text: str, **kwargs) -> AgentRunResult:
        run_id = str(uuid4())
        await self.hooks.dispatch(
            HookEvent.FLOW_START,
            {
                "flow_name": self.name,
                "run_id": run_id,
                "step_count": len(self.steps),
            },
        )

        accumulated_output = input_text
        last_result: Optional[AgentRunResult] = None
        combined_usage = AggregatedUsage()

        for step in self.steps:
            step_input = accumulated_output
            if isinstance(step, BaseAgent):
                result = await step.run(step_input, **kwargs)
            else:  # nested BaseFlow
                result = await step.run(step_input, **kwargs)

            last_result = result
            combined_usage.add(result.usage)

            # Extract text output to pass to next step
            step_output = result.output
            if isinstance(step_output, list):
                step_output = "\n".join(
                    str(p) for p in step_output if isinstance(p, str)
                )
            accumulated_output = (
                f"{accumulated_output}\n\n{step_output}"
                if step_output
                else accumulated_output
            )

        await self.hooks.dispatch(
            HookEvent.FLOW_END,
            {
                "flow_name": self.name,
                "run_id": run_id,
                "status": last_result.status.value if last_result else "unknown",
            },
        )

        if last_result is None:
            return AgentRunResult(
                run_id=run_id,
                agent_name=self.name,
                output=[],
                status=RunStatus.ERROR,
                usage=combined_usage,
            )

        # Return the last result but annotate it with this flow's identity
        return AgentRunResult(
            run_id=run_id,
            agent_name=self.name,
            output=last_result.output,
            status=last_result.status,
            steps=last_result.steps,
            usage=combined_usage,
            tool_calls_total=last_result.tool_calls_total,
            tool_calls_by_name=last_result.tool_calls_by_name,
            start_time=last_result.start_time,
            end_time=last_result.end_time,
            duration_seconds=last_result.duration_seconds,
            max_iterations=last_result.max_iterations,
        )

    async def run_stream(self, input_text: str, **kwargs) -> AsyncIterator[Any]:
        run_id = str(uuid4())
        await self.hooks.dispatch(
            HookEvent.FLOW_START,
            {
                "flow_name": self.name,
                "run_id": run_id,
            },
        )

        accumulated_output = input_text

        for step in self.steps:
            agent_id = (
                step.name if isinstance(step, (BaseAgent, BaseFlow)) else "unknown"
            )
            step_input = accumulated_output
            partial_chunks: List[str] = []

            if isinstance(step, BaseAgent):
                stream = step.run_stream(step_input, **kwargs)
            else:
                stream = step.run_stream(step_input, **kwargs)

            async for chunk in stream:
                # Tag every chunk with the producing agent's id
                if hasattr(chunk, "__dict__"):
                    chunk_dict = (
                        chunk.__dict__.copy() if hasattr(chunk, "__dict__") else {}
                    )
                    chunk_dict["agent_id"] = agent_id
                    chunk.__dict__.update(chunk_dict)
                yield chunk

                # Accumulate text for next step's input
                from agent_framework.core.messages._types import TextDeltaChunk

                if isinstance(chunk, TextDeltaChunk):
                    partial_chunks.append(chunk.text)

            if partial_chunks:
                accumulated_output = (
                    f"{accumulated_output}\n\n{''.join(partial_chunks)}"
                )

        await self.hooks.dispatch(
            HookEvent.FLOW_END,
            {
                "flow_name": self.name,
                "run_id": run_id,
            },
        )

    def to_graph(self) -> FlowGraph:
        graph = FlowGraph(name=self.name)
        graph.add_node(FlowNode(id="__input__", label="start", node_type="input"))

        prev_id = "__input__"
        for i, step in enumerate(self.steps):
            node_id = step.name if hasattr(step, "name") else f"step_{i}"
            node_type: Any = "flow" if isinstance(step, BaseFlow) else "agent"
            graph.add_node(FlowNode(id=node_id, label=node_id, node_type=node_type))
            graph.add_edge(FlowEdge(source=prev_id, target=node_id))
            prev_id = node_id

        graph.add_node(FlowNode(id="__output__", label="end", node_type="output"))
        graph.add_edge(FlowEdge(source=prev_id, target="__output__"))
        return graph


# ---------------------------------------------------------------------------
# ParallelFlow
# ---------------------------------------------------------------------------


class ParallelFlow(BaseFlow):
    """Run all branches concurrently and merge their outputs.

    Merge strategies
    ----------------
    ``"concat"`` (default)
        Join all outputs with ``\\n\\n`` in branch order.

    ``"vote"``
        Return the output that appears most frequently (majority vote).
        Ties are broken by branch order.

    ``Callable[[List[str]], str]``
        Custom merge function — receives ordered list of branch outputs,
        returns a single string.

    Args:
        branches:        List of agents / flows to run in parallel.
        name:            Flow identifier.
        description:     Human-readable purpose.
        merge:           Merge strategy.  Defaults to ``"concat"``.
        hooks:           Optional hook manager.
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

            counts = Counter(outputs)
            return counts.most_common(1)[0][0]
        # Default: concat
        return "\n\n".join(outputs)

    async def run(self, input_text: str, **kwargs) -> AgentRunResult:
        run_id = str(uuid4())
        await self.hooks.dispatch(
            HookEvent.FLOW_START,
            {
                "flow_name": self.name,
                "run_id": run_id,
                "branch_count": len(self.branches),
            },
        )

        tasks = [
            (
                step.run(input_text, **kwargs)
                if isinstance(step, BaseAgent)
                else step.run(input_text, **kwargs)
            )
            for step in self.branches
        ]
        results: List[AgentRunResult] = await asyncio.gather(*tasks)

        combined_usage = AggregatedUsage()
        branch_outputs: List[str] = []
        for r in results:
            combined_usage.add(r.usage)
            out = r.output
            if isinstance(out, list):
                out = "\n".join(str(p) for p in out if isinstance(p, str))
            branch_outputs.append(out or "")

        merged = self._merge_outputs(branch_outputs)

        await self.hooks.dispatch(
            HookEvent.FLOW_END,
            {
                "flow_name": self.name,
                "run_id": run_id,
            },
        )

        return AgentRunResult(
            run_id=run_id,
            agent_name=self.name,
            output=[merged],
            status=RunStatus.COMPLETED,
            usage=combined_usage,
        )

    async def run_stream(self, input_text: str, **kwargs) -> AsyncIterator[Any]:
        run_id = str(uuid4())
        await self.hooks.dispatch(
            HookEvent.FLOW_START,
            {
                "flow_name": self.name,
                "run_id": run_id,
            },
        )

        # Collect all branch streams concurrently using async queues
        async def _drain(step: FlowStep, q: asyncio.Queue) -> None:
            agent_id = step.name if hasattr(step, "name") else "branch"
            try:
                stream = (
                    step.run_stream(input_text, **kwargs)
                    if isinstance(step, BaseAgent)
                    else step.run_stream(input_text, **kwargs)
                )
                async for chunk in stream:
                    if hasattr(chunk, "__dict__"):
                        chunk.__dict__["agent_id"] = agent_id
                    await q.put(chunk)
            finally:
                await q.put(None)  # sentinel

        queue: asyncio.Queue = asyncio.Queue()
        drain_tasks = [
            asyncio.create_task(_drain(step, queue)) for step in self.branches
        ]
        done_count = 0
        total = len(self.branches)

        while done_count < total:
            item = await queue.get()
            if item is None:
                done_count += 1
            else:
                yield item

        await asyncio.gather(*drain_tasks, return_exceptions=True)
        await self.hooks.dispatch(
            HookEvent.FLOW_END,
            {
                "flow_name": self.name,
                "run_id": run_id,
            },
        )

    def to_graph(self) -> FlowGraph:
        graph = FlowGraph(name=self.name)
        graph.add_node(FlowNode(id="__input__", label="start", node_type="input"))
        graph.add_node(FlowNode(id="__output__", label="end", node_type="output"))

        for i, step in enumerate(self.branches):
            node_id = step.name if hasattr(step, "name") else f"branch_{i}"
            node_type: Any = "flow" if isinstance(step, BaseFlow) else "agent"
            graph.add_node(FlowNode(id=node_id, label=node_id, node_type=node_type))
            graph.add_edge(FlowEdge(source="__input__", target=node_id))
            graph.add_edge(FlowEdge(source=node_id, target="__output__"))

        return graph


# ---------------------------------------------------------------------------
# ConditionalFlow
# ---------------------------------------------------------------------------


class ConditionalFlow(BaseFlow):
    """Route execution to one of two sub-flows based on a predicate.

    The ``predicate`` callable receives the current input string and returns
    ``True`` to take the ``if_true`` branch or ``False`` for ``if_false``.

    Args:
        predicate:   ``(input_text: str) -> bool`` — called at runtime.
        if_true:     Agent or flow to run when predicate is truthy.
        if_false:    Agent or flow to run when predicate is falsy.
        name:        Flow identifier.
        description: Human-readable purpose.
        hooks:       Optional hook manager.

    Example::

        is_code_question = lambda text: any(
            kw in text.lower() for kw in ("code", "python", "function", "debug")
        )
        flow = ConditionalFlow(
            predicate=is_code_question,
            if_true=code_agent,
            if_false=general_agent,
        )
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

    def _select_branch(self, input_text: str) -> FlowStep:
        try:
            result = self.predicate(input_text)
        except Exception as exc:
            logger.warning(
                "[%s] Predicate raised %s — defaulting to if_false branch",
                self.name,
                exc,
            )
            result = False
        return self.if_true if result else self.if_false

    async def run(self, input_text: str, **kwargs) -> AgentRunResult:
        run_id = str(uuid4())
        await self.hooks.dispatch(
            HookEvent.FLOW_START,
            {
                "flow_name": self.name,
                "run_id": run_id,
            },
        )

        branch = self._select_branch(input_text)
        branch_name = branch.name if hasattr(branch, "name") else str(branch)
        logger.debug("[%s] routing to branch: %s", self.name, branch_name)

        if isinstance(branch, BaseAgent):
            result = await branch.run(input_text, **kwargs)
        else:
            result = await branch.run(input_text, **kwargs)

        await self.hooks.dispatch(
            HookEvent.FLOW_END,
            {
                "flow_name": self.name,
                "run_id": run_id,
                "branch_taken": branch_name,
            },
        )
        return result

    async def run_stream(self, input_text: str, **kwargs) -> AsyncIterator[Any]:
        run_id = str(uuid4())
        await self.hooks.dispatch(
            HookEvent.FLOW_START,
            {
                "flow_name": self.name,
                "run_id": run_id,
            },
        )

        branch = self._select_branch(input_text)
        agent_id = branch.name if hasattr(branch, "name") else "branch"

        stream = (
            branch.run_stream(input_text, **kwargs)
            if isinstance(branch, BaseAgent)
            else branch.run_stream(input_text, **kwargs)
        )
        async for chunk in stream:
            if hasattr(chunk, "__dict__"):
                chunk.__dict__["agent_id"] = agent_id
            yield chunk

        await self.hooks.dispatch(
            HookEvent.FLOW_END,
            {
                "flow_name": self.name,
                "run_id": run_id,
                "branch_taken": agent_id,
            },
        )

    def to_graph(self) -> FlowGraph:
        graph = FlowGraph(name=self.name)
        graph.add_node(FlowNode(id="__input__", label="start", node_type="input"))

        cond_id = f"{self.name}__condition"
        graph.add_node(FlowNode(id=cond_id, label="predicate?", node_type="condition"))
        graph.add_edge(FlowEdge(source="__input__", target=cond_id))

        true_id = self.if_true.name if hasattr(self.if_true, "name") else "if_true"
        false_id = self.if_false.name if hasattr(self.if_false, "name") else "if_false"

        true_type: Any = "flow" if isinstance(self.if_true, BaseFlow) else "agent"
        false_type: Any = "flow" if isinstance(self.if_false, BaseFlow) else "agent"

        graph.add_node(FlowNode(id=true_id, label=true_id, node_type=true_type))
        graph.add_node(FlowNode(id=false_id, label=false_id, node_type=false_type))
        graph.add_edge(FlowEdge(source=cond_id, target=true_id, label="yes"))
        graph.add_edge(FlowEdge(source=cond_id, target=false_id, label="no"))

        graph.add_node(FlowNode(id="__output__", label="end", node_type="output"))
        graph.add_edge(FlowEdge(source=true_id, target="__output__"))
        graph.add_edge(FlowEdge(source=false_id, target="__output__"))

        return graph
