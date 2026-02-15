"""Evaluation runner — orchestrates agent execution + judging.

Runs an agent against a dataset, collects outputs, scores them
with the judge, and produces an EvalReport.

Features:
  - Sequential or parallel case execution
  - Per-case timeout protection
  - Automatic retry on transient errors
  - Progress callbacks for UI integration
  - Export to JSON / console / markdown
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from agent_framework.agents.base_agent import BaseAgent
from agent_framework.evals.judge import LLMJudge
from agent_framework.evals.models import (
    EvalCase,
    EvalCaseResult,
    EvalDataset,
    EvalReport,
)

logger = logging.getLogger("agent_framework.evals")


class EvalRunner:
    """Executes evaluation suites against an agent.

    Usage::

        runner = EvalRunner(agent=my_agent, judge=my_judge)
        report = await runner.run(dataset)
        print(report.summary())
        runner.export_json(report, "results/eval_run.json")
    """

    def __init__(
        self,
        agent: BaseAgent,
        judge: LLMJudge,
        *,
        concurrency: int = 1,
        case_timeout: float = 120.0,
        max_retries: int = 1,
        reset_agent: bool = True,
        on_case_complete: Optional[Callable[[EvalCaseResult, int, int], None]] = None,
    ):
        """
        Args:
            agent: The agent to evaluate.
            judge: The LLM judge to score outputs.
            concurrency: Max parallel case executions (1 = sequential).
            case_timeout: Seconds before a single case times out.
            max_retries: Retry count for transient agent failures.
            reset_agent: Whether to reset agent state between cases.
            on_case_complete: Callback(result, current, total) for progress.
        """
        self.agent = agent
        self.judge = judge
        self.concurrency = max(1, concurrency)
        self.case_timeout = case_timeout
        self.max_retries = max_retries
        self.reset_agent = reset_agent
        self.on_case_complete = on_case_complete

    async def run(
        self,
        dataset: EvalDataset,
        **agent_kwargs,
    ) -> EvalReport:
        """Run the full evaluation suite.

        Args:
            dataset: The evaluation dataset.
            **agent_kwargs: Extra kwargs passed to agent.run().

        Returns:
            EvalReport with all results and aggregate metrics.
        """
        start_time = datetime.utcnow()
        t0 = time.monotonic()

        logger.info(
            f"Starting eval: {dataset.name} "
            f"({dataset.size} cases, concurrency={self.concurrency})"
        )

        results: List[EvalCaseResult] = []

        if self.concurrency == 1:
            # Sequential execution
            for idx, case in enumerate(dataset.cases):
                result = await self._run_single_case(case, **agent_kwargs)
                results.append(result)
                if self.on_case_complete:
                    self.on_case_complete(result, idx + 1, dataset.size)
                logger.info(
                    f"  [{idx + 1}/{dataset.size}] "
                    f"{'PASS' if result.passed else 'FAIL'} "
                    f"(avg={result.avg_score:.2f}) "
                    f"{case.input[:50]}..."
                )
        else:
            # Parallel execution with semaphore
            sem = asyncio.Semaphore(self.concurrency)
            completed = 0

            async def run_with_sem(case: EvalCase) -> EvalCaseResult:
                nonlocal completed
                async with sem:
                    result = await self._run_single_case(case, **agent_kwargs)
                    completed += 1
                    if self.on_case_complete:
                        self.on_case_complete(result, completed, dataset.size)
                    return result

            tasks = [run_with_sem(case) for case in dataset.cases]
            results = await asyncio.gather(*tasks)

        end_time = datetime.utcnow()
        total_duration = time.monotonic() - t0

        report = EvalReport(
            dataset_name=dataset.name,
            agent_name=self.agent.name,
            model=getattr(self.agent.model_client, "model", "unknown"),
            results=list(results),
            start_time=start_time,
            end_time=end_time,
            total_duration_seconds=total_duration,
            criteria_names=[c.name for c in self.judge.criteria],
            metadata=dataset.metadata,
        )

        logger.info(f"Eval complete:\n{report.summary()}")
        return report

    async def _run_single_case(
        self,
        case: EvalCase,
        **agent_kwargs,
    ) -> EvalCaseResult:
        """Run agent + judge on a single eval case."""
        # Reset agent state
        if self.reset_agent:
            self.agent.reset()

        # Run agent with retries
        actual_output = ""
        run_id = ""
        status = "completed"
        steps_used = 0
        tool_calls_total = 0
        tool_calls_by_name: Dict[str, int] = {}
        tokens_used = 0
        duration = 0.0
        error_msg: Optional[str] = None

        for attempt in range(self.max_retries + 1):
            try:
                agent_result = await asyncio.wait_for(
                    self.agent.run(case.input, **agent_kwargs),
                    timeout=self.case_timeout,
                )

                actual_output = agent_result.output_text
                run_id = agent_result.run_id
                status = agent_result.status.value
                steps_used = agent_result.steps_used
                tool_calls_total = agent_result.tool_calls_total
                tool_calls_by_name = agent_result.tool_calls_by_name
                tokens_used = agent_result.usage.total_tokens
                duration = agent_result.duration_seconds or 0.0
                error_msg = agent_result.error
                break

            except asyncio.TimeoutError:
                error_msg = f"Case timed out after {self.case_timeout}s"
                status = "timeout"
                if attempt < self.max_retries:
                    logger.warning(f"Case {case.case_id} timeout, retrying...")
                    if self.reset_agent:
                        self.agent.reset()
                    continue
                logger.error(f"Case {case.case_id} failed: {error_msg}")

            except Exception as e:
                error_msg = str(e)
                status = "error"
                if attempt < self.max_retries:
                    logger.warning(f"Case {case.case_id} error, retrying: {e}")
                    if self.reset_agent:
                        self.agent.reset()
                    continue
                logger.error(f"Case {case.case_id} failed: {e}")

        # Score with judge (skip if agent errored with no output)
        scores = []
        if actual_output and not error_msg:
            try:
                scores = await self.judge.score(
                    input_text=case.input,
                    actual_output=actual_output,
                    expected_output=case.expected_output,
                    context=case.context,
                )
            except Exception as e:
                logger.error(f"Judge failed for case {case.case_id}: {e}")
                error_msg = f"Judge error: {e}"

        return EvalCaseResult(
            case_id=case.case_id,
            input=case.input,
            expected_output=case.expected_output,
            actual_output=actual_output,
            scores=scores,
            run_id=run_id,
            status=status,
            steps_used=steps_used,
            tool_calls_total=tool_calls_total,
            tool_calls_by_name=tool_calls_by_name,
            tokens_used=tokens_used,
            duration_seconds=duration,
            error=error_msg,
            tags=case.tags,
        )

    # -- Export helpers --------------------------------------------------------

    @staticmethod
    def export_json(report: EvalReport, path: str | Path) -> None:
        """Export report to a JSON file."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(report.to_dict(), f, indent=2, default=str)
        logger.info(f"Report exported to {path}")

    @staticmethod
    def export_markdown(report: EvalReport, path: str | Path) -> None:
        """Export report as a markdown table."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        lines = [
            f"# Eval Report: {report.dataset_name}",
            f"",
            f"**Agent:** {report.agent_name}  ",
            f"**Model:** {report.model}  ",
            f"**Pass Rate:** {report.pass_rate:.1%}  ",
            f"**Avg Score:** {report.avg_score:.3f}  ",
            f"**Total Tokens:** {report.total_tokens:,}  ",
            f"**Duration:** {report.total_duration_seconds:.1f}s  ",
            f"",
            "## Results",
            "",
            "| # | Input | Expected | Actual | Score | Pass | Latency |",
            "|---|-------|----------|--------|-------|------|---------|",
        ]

        for i, r in enumerate(report.results, 1):
            input_short = r.input[:40].replace("|", "\\|")
            expected_short = (r.expected_output or "")[:30].replace("|", "\\|")
            actual_short = r.actual_output[:30].replace("|", "\\|")
            status = "PASS" if r.passed else "FAIL"
            lines.append(
                f"| {i} | {input_short} | {expected_short} | "
                f"{actual_short} | {r.avg_score:.2f} | {status} | "
                f"{r.duration_seconds:.1f}s |"
            )

        # Criterion breakdown
        by_criterion = report.scores_by_criterion()
        if by_criterion:
            lines.extend(["", "## Scores by Criterion", ""])
            lines.append("| Criterion | Mean | Min | Max | Pass Rate |")
            lines.append("|-----------|------|-----|-----|-----------|")
            for name, stats in by_criterion.items():
                lines.append(
                    f"| {name} | {stats['mean']:.3f} | {stats['min']:.3f} | "
                    f"{stats['max']:.3f} | {stats['pass_rate']:.1%} |"
                )

        lines.append("")
        path.write_text("\n".join(lines), encoding="utf-8")
        logger.info(f"Markdown report exported to {path}")
