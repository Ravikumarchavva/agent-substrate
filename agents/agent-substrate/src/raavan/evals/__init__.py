"""Evaluation framework for measuring agent quality.

Provides:
  - EvalCase / EvalDataset  — define what to test
  - LLMJudge                — grade outputs using an LLM
  - EvalRunner              — execute eval suites
  - EvalResult / EvalReport — structured results with metrics
  - Built-in criteria       — correctness, helpfulness, safety, relevance

Quick start::

    from raavan.evals import (
        EvalCase, EvalDataset, LLMJudge, EvalRunner, CORRECTNESS,
    )

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

from raavan.evals.models import (
    EvalCase,
    EvalDataset,
    EvalScore,
    EvalCaseResult,
    EvalReport,
)
from raavan.evals.criteria import (
    EvalCriterion,
    CORRECTNESS,
    HELPFULNESS,
    RELEVANCE,
    SAFETY,
    CONCISENESS,
    TOOL_USAGE,
)
from raavan.evals.judge import LLMJudge
from raavan.evals.runner import EvalRunner

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
