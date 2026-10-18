"""Evals: measure an agent against cases.

An eval is a dataset of cases, an agent, and (optionally) a judge — another model that scores each answer against
criteria such as correctness. Each case runs on a fresh runtime, so one case cannot leak into the next.

    uv run python examples/09_evals.py
"""

from __future__ import annotations

import asyncio

from _model import ScriptedModel, pick_model

from substrate.agents import ReActAgent
from substrate.evals import CORRECTNESS, EvalCase, EvalDataset, EvalRunner, LLMJudge


async def main() -> None:
    agent = ReActAgent(
        "arithmetic",
        model=pick_model("4", "Paris", "I am not sure."),
        system_instructions="Answer with just the answer.",
    )
    dataset = EvalDataset(
        name="smoke",
        cases=[
            EvalCase(input="What is 2 + 2?", expected_output="4", tags=["math"]),
            EvalCase(input="Capital of France?", expected_output="Paris", tags=["geography"]),
            EvalCase(input="Capital of Australia?", expected_output="Canberra", tags=["geography"]),
        ],
    )

    # A real judge is a strong model: ``LLMJudge(pick_model(...), ...)``. Offline, it answers with fixed JSON scores.
    judge_model = ScriptedModel(
        '{"score": 5, "reasoning": "matches"}',
        '{"score": 5, "reasoning": "matches"}',
        '{"score": 1, "reasoning": "wrong city"}',
    )
    judge = LLMJudge(judge_model, criteria=[CORRECTNESS])

    report = await EvalRunner(agent, judge).run(dataset)
    print(report.summary())


if __name__ == "__main__":
    asyncio.run(main())
