"""EvalRunner — execute evaluation suites against kernel-native agents.

Accepts any kernel Agent (``id: AgentId``, ``run(ctx, inbox) -> None``).
Drives the agent through an in-memory Runtime, collects the reply via the
signal bus, and returns a structured EvalReport.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from ravi.agents.runtime.runtime import Runtime
from ravi.kernel.core.content import ChatMessage, Role, TextBlock
from ravi.kernel.messaging.message import ChatPayload, Message
from ravi.kernel.runtime.ids import new_run_id

from ravi.fabric.evals.judge import LLMJudge
from ravi.fabric.evals.models import EvalCase, EvalCaseResult, EvalDataset, EvalReport

if TYPE_CHECKING:
    from ravi.kernel.runtime.agent import Agent

logger = logging.getLogger(__name__)


class EvalRunner:
    """Execute an eval dataset against a kernel Agent and return a structured report.

    Parameters
    ----------
    agent:       Kernel Agent (``id: AgentId``, ``run(ctx, inbox) -> None``).
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
        try:
            await rt.submit(self._agent.id, msg)
            payload = await asyncio.wait_for(
                rt.signal_bus.wait_for_signal(sentinel_run_id, f"reply:{cid}"),
                timeout=self._timeout,
            )
            output = str(payload.get("text", ""))
            duration = time.monotonic() - start
            case_result = EvalCaseResult(
                case_id=case.case_id,
                input=case.input,
                expected_output=case.expected_output,
                actual_output=output,
                status="completed",
                duration_seconds=duration,
                tags=list(case.tags),
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
            )

        if self._judge and case_result.status != "error":
            scores = await self._judge.score(
                input_text=case.input,
                actual_output=case_result.actual_output,
                expected_output=case.expected_output,
                context=case.context,
            )
            case_result.scores = list(scores)

        return case_result
