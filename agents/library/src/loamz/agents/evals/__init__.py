"""Evaluation framework for measuring agent quality.

Provides:
  - EvalCase / EvalDataset  — define what to test
  - LLMJudge                — grade outputs using an LLM
  - EvalRunner              — execute eval suites
  - EvalResult / EvalReport — structured results with metrics
  - Built-in criteria       — correctness, helpfulness, safety, relevance

Quick start::

    from loamz.agents.evals import EvalCase, EvalDataset, LLMJudge, EvalRunner, CORRECTNESS

    dataset = EvalDataset(cases=[
        EvalCase(
            input="What is 2+2?",
            expected_output="4",
            tags=["math"],
        ),
    ])

    judge = LLMJudge(model_client=my_openai_client, criteria=[CORRECTNESS])
    runner = EvalRunner(agent=my_agent, judge=judge)
    report = await runner.run(dataset)
    print(report.summary())
"""

from __future__ import annotations

from loamz.agents.evals.criteria import (
    CONCISENESS,
    CORRECTNESS,
    HELPFULNESS,
    RELEVANCE,
    SAFETY,
    TOOL_USAGE,
    EvalCriterion,
)
from loamz.agents.evals.judge import LLMJudge
from loamz.agents.evals.models import EvalCase, EvalCaseResult, EvalDataset, EvalReport, EvalScore
from loamz.agents.evals.runner import EvalRunner

__all__ = [
    # Models
    "EvalCase",
    "EvalDataset",
    "EvalScore",
    "EvalCaseResult",
    "EvalReport",
    # Criteria
    "EvalCriterion",
    "CORRECTNESS",
    "HELPFULNESS",
    "RELEVANCE",
    "SAFETY",
    "CONCISENESS",
    "TOOL_USAGE",
    # Judge
    "LLMJudge",
    # Runner
    "EvalRunner",
]
