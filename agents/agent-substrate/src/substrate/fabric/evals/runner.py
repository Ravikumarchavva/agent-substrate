"""EvalRunner — execute evaluation suites against kernel-native agents.

Accepts any real ``Agent`` (``id: AgentId``, ``run(ctx: RunContext, inbox) ->
None``) — drives it through a real in-memory ``Runtime``, so it needs the
concrete ``RunContext``-typed agent, not just kernel's minimal ``Agent``
bound. Collects the reply via the signal bus and returns a structured
EvalReport.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from substrate.agents.runtime.runtime import Runtime
from substrate.kernel.core.content import ChatMessage, Role, TextBlock
from substrate.kernel.messaging.message import ChatPayload, Message
from substrate.kernel.runtime.ids import RunId, new_run_id

from substrate.fabric.evals.judge import LLMJudge
from substrate.fabric.evals.models import (
    EvalCase,
    EvalCaseResult,
    EvalDataset,
    EvalReport,
)

if TYPE_CHECKING:
    from substrate.agents.runtime.context import Agent

logger = logging.getLogger(__name__)


@dataclass
class _Trace:
    """Execution-trace aggregates read back from a run's event log.

    Captures the agent-under-test's own run only. For an orchestrator, a
    sub-agent's LLM/tool calls live in its child run and are not included.
    """

    steps: int = 0
    tokens: int = 0
    tool_total: int = 0
    tool_by_name: dict[str, int] = field(default_factory=dict)
    tool_order: list[str] = field(default_factory=list)


class EvalRunner:
    """Execute an eval dataset against an agent and return a structured report.

    Parameters
    ----------
    agent:       Agent (``id: AgentId``, ``run(ctx: RunContext, inbox) -> None``).
    judge:       Optional LLMJudge for scoring outputs.
    concurrency: Number of cases to run in parallel (default 1 = sequential).
    timeout:     Per-case timeout in seconds.  ``None`` = no timeout.
    """

    def __init__(
        self,
        agent: Agent,
        judge: LLMJudge | None = None,
        *,
        concurrency: int = 1,
        timeout: float | None = None,
    ) -> None:
        self._agent = agent
        self._judge = judge
        self._concurrency = max(1, concurrency)
        self._timeout = timeout

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(self, dataset: EvalDataset) -> EvalReport:
        """Run all cases in the dataset and return an aggregated EvalReport."""
        wall_start = time.monotonic()
        async with Runtime() as rt:
            await rt.register(self._agent)

            if self._concurrency == 1:
                results = [await self._run_case(case, rt=rt) for case in dataset.cases]
            else:
                sem = asyncio.Semaphore(self._concurrency)

                async def _guarded(case: EvalCase) -> EvalCaseResult:
                    async with sem:
                        return await self._run_case(case, rt=rt)

                results = list(
                    await asyncio.gather(*[_guarded(c) for c in dataset.cases])
                )

        total_duration = time.monotonic() - wall_start
        criteria_names = [c.name for c in self._judge.criteria] if self._judge else []
        return EvalReport(
            dataset_name=dataset.name,
            results=results,
            criteria_names=criteria_names,
            total_duration_seconds=total_duration,
        )

    async def run_case(self, case: EvalCase) -> EvalCaseResult:
        """Run a single case with its own ephemeral Runtime."""
        async with Runtime() as rt:
            await rt.register(self._agent)
            return await self._run_case(case, rt=rt)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _run_case(self, case: EvalCase, *, rt: Runtime) -> EvalCaseResult:
        sentinel_run_id = new_run_id()
        msg = Message(
            target=self._agent.id,
            payload=ChatPayload(
                message=ChatMessage(
                    role=Role.USER, content=[TextBlock(text=case.input)]
                )
            ),
            reply_to=sentinel_run_id,
        )
        cid = msg.correlation_id
        start = time.monotonic()
        run_id: RunId = ""
        trace = _Trace()
        try:
            run_id = await rt.submit(self._agent.id, msg)
            # Poll consume() rather than block: SignalBusProtocol is consume-based
            # (matches the durable backend, which has no way to "block" on a
            # DB row). sentinel_run_id is a synthetic mailbox key for this
            # external harness, not a real tracked run, so a fixed effect_id
            # is fine — this call site never replays.
            deadline = (
                time.monotonic() + self._timeout if self._timeout is not None else None
            )
            payload: dict | None = None
            while deadline is None or time.monotonic() < deadline:
                payload = await rt.signal_bus.consume(
                    sentinel_run_id,
                    f"reply:{cid}",
                    f"eval-wait:{sentinel_run_id}:{cid}",
                )
                if payload is not None:
                    break
                await asyncio.sleep(0.05)
            if payload is None:
                raise TimeoutError(
                    f"Timed out after {self._timeout}s waiting for a reply"
                )
            output = str(payload.get("text", ""))
            duration = time.monotonic() - start
            trace = await self._collect_trace(rt, run_id)
            case_result = EvalCaseResult(
                case_id=case.case_id,
                input=case.input,
                expected_output=case.expected_output,
                actual_output=output,
                status="completed",
                duration_seconds=duration,
                tags=list(case.tags),
                run_id=run_id,
                steps_used=trace.steps,
                tokens_used=trace.tokens,
                tool_calls_total=trace.tool_total,
                tool_calls_by_name=trace.tool_by_name,
            )
        except Exception as exc:
            duration = time.monotonic() - start
            logger.warning("[EvalRunner] case %s failed: %s", case.case_id, exc)
            case_result = EvalCaseResult(
                case_id=case.case_id,
                input=case.input,
                expected_output=case.expected_output,
                actual_output="",
                status="error",
                error=str(exc),
                duration_seconds=duration,
                tags=list(case.tags),
                run_id=run_id,
            )

        if self._judge and case_result.status != "error":
            scores = await self._judge.score(
                input_text=case.input,
                actual_output=case_result.actual_output,
                expected_output=case.expected_output,
                context=case.context,
                expected_tool_calls=case.expected_tool_calls,
                actual_tool_calls=trace.tool_order,
            )
            case_result.scores = list(scores)

        return case_result

    @staticmethod
    async def _collect_trace(rt: Runtime, run_id: RunId) -> _Trace:
        """Aggregate steps / tokens / tool calls from the run's event log.

        Read once the reply has arrived: every ``llm.call`` and ``tool.call``
        entry is journaled during the agent loop, before it replies.
        """
        trace = _Trace()
        async for entry in rt.event_log.read(run_id):
            payload = entry.payload or {}
            if entry.kind == "llm.call":
                trace.steps += 1
                trace.tokens += int(payload.get("tokens", 0) or 0)
            elif entry.kind == "tool.call":
                name = str(payload.get("tool_name", "tool"))
                trace.tool_total += 1
                trace.tool_by_name[name] = trace.tool_by_name.get(name, 0) + 1
                trace.tool_order.append(name)
        return trace
