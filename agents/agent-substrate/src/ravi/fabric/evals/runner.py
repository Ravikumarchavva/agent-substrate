"""EvalRunner — execute evaluation suites against any flow-compatible agent.

Accepts any object with ``async run(input_text: str, **kw) -> AgentRunResult``
— the same duck-typed interface used by every built-in flow.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter
from typing import Any, Optional

from ravi.fabric.evals.judge import LLMJudge
from ravi.fabric.evals.models import EvalCase, EvalCaseResult, EvalDataset, EvalReport

logger = logging.getLogger(__name__)


class EvalRunner:
    """Execute an eval dataset against an agent and return a structured report.

    Parameters
    ----------
    agent:       Any object with ``async run(input_text, **kw) -> AgentRunResult``.
    judge:       Optional LLMJudge for scoring outputs.
    concurrency: Number of cases to run in parallel (default 1 = sequential).
    timeout:     Per-case timeout in seconds.  ``None`` = no timeout.
    """

    def __init__(
        self,
        agent: Any,
        judge: Optional[LLMJudge] = None,
        *,
        concurrency: int = 1,
        timeout: Optional[float] = None,
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

        if self._concurrency == 1:
            results = [await self.run_case(case) for case in dataset.cases]
        else:
            sem = asyncio.Semaphore(self._concurrency)

            async def _guarded(case: EvalCase) -> EvalCaseResult:
                async with sem:
                    return await self.run_case(case)

            results = list(
                await asyncio.gather(*[_guarded(c) for c in dataset.cases])
            )

        total_duration = time.monotonic() - wall_start
        criteria_names = (
            [c.name for c in self._judge.criteria] if self._judge else []
        )
        return EvalReport(
            dataset_name=dataset.name,
            results=results,
            criteria_names=criteria_names,
            total_duration_seconds=total_duration,
        )

    async def run_case(self, case: EvalCase) -> EvalCaseResult:
        """Run a single eval case and return its result."""
        start = time.monotonic()
        try:
            result = await asyncio.wait_for(
                self._agent.run(case.input),
                timeout=self._timeout,
            )
            duration = time.monotonic() - start

            # Build tool_calls_by_name count
            tool_calls_by_name: dict[str, int] = dict(
                Counter(tc.name for tc in result.tool_calls)
            )

            case_result = EvalCaseResult(
                case_id=case.case_id,
                input=case.input,
                expected_output=case.expected_output,
                actual_output=result.output,
                status=result.status,
                run_id=result.run_id,
                tool_calls_total=len(result.tool_calls),
                tool_calls_by_name=tool_calls_by_name,
                duration_seconds=duration,
                tags=list(case.tags),
            )

        except Exception as exc:  # includes asyncio.TimeoutError
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

        # Score with judge if provided and run succeeded
        if self._judge and case_result.status != "error":
            scores = await self._judge.score(
                input_text=case.input,
                actual_output=case_result.actual_output,
                expected_output=case.expected_output,
                context=case.context,
            )
            case_result.scores = list(scores)

        return case_result
