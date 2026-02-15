"""Example: Running evaluations on your agent.

Demonstrates:
  1. Defining eval cases (test dataset)
  2. Setting up an LLM judge with built-in criteria
  3. Running the eval suite
  4. Exporting results (JSON + Markdown)
  5. Using lifecycle hooks for cost tracking

Usage:
    python examples/eval_example.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agent_framework.agents.react_agent import ReActAgent
from agent_framework.model_clients.openai.openai_client import OpenAIClient
from agent_framework.tools.builtin_tools import CalculatorTool, GetCurrentTimeTool
from agent_framework.hooks import HookEvent, HookManager, CostTracker

# Evals
from agent_framework.evals import (
    EvalCase,
    EvalDataset,
    EvalRunner,
    LLMJudge,
    CORRECTNESS,
    HELPFULNESS,
    SAFETY,
    CONCISENESS,
)


async def main():
    # ── 1. Define your test cases ─────────────────────────────────────────
    dataset = EvalDataset(
        name="basic_math_qa",
        description="Tests basic math and general knowledge capabilities",
        cases=[
            EvalCase(
                input="What is 15 * 7?",
                expected_output="105",
                tags=["math", "easy"],
            ),
            EvalCase(
                input="Calculate the square root of 144",
                expected_output="12",
                tags=["math", "easy"],
            ),
            EvalCase(
                input="What is 2^10?",
                expected_output="1024",
                tags=["math", "medium"],
            ),
            EvalCase(
                input="What is the current time?",
                expected_output=None,  # No expected — just check helpfulness
                tags=["tools", "time"],
            ),
            EvalCase(
                input="If a train travels 60 mph for 2.5 hours, how far does it go?",
                expected_output="150 miles",
                tags=["math", "word_problem"],
            ),
        ],
    )

    print(f"Dataset: {dataset.name} ({dataset.size} cases)")

    # ── 2. Set up the agent ───────────────────────────────────────────────
    # Agent model (the model being tested)
    agent_client = OpenAIClient(model="gpt-4.1-nano")
    
    # Set up lifecycle hooks with cost tracking
    hooks = HookManager()
    cost_tracker = CostTracker(model="gpt-4.1-nano")
    hooks.register(HookEvent.LLM_END, cost_tracker.on_llm_end)
    hooks.register(HookEvent.RUN_END, cost_tracker.on_run_end)

    agent = ReActAgent(
        name="eval-agent",
        description="Agent under evaluation",
        model_client=agent_client,
        tools=[CalculatorTool(), GetCurrentTimeTool()],
        hooks=hooks,
        max_iterations=5,
        verbose=False,  # Quiet during evals
    )

    # ── 3. Set up the judge ───────────────────────────────────────────────
    # Judge model (typically stronger than the agent model)
    judge_client = OpenAIClient(model="gpt-4.1-mini")
    
    judge = LLMJudge(
        model_client=judge_client,
        criteria=[CORRECTNESS, HELPFULNESS, SAFETY, CONCISENESS],
        parallel=True,  # Score all criteria in parallel
    )

    # ── 4. Run the evaluation ─────────────────────────────────────────────
    runner = EvalRunner(
        agent=agent,
        judge=judge,
        concurrency=1,         # Sequential for deterministic results
        case_timeout=60.0,     # 60s max per case
        reset_agent=True,      # Fresh state per case
    )

    print("\nRunning evaluation...\n")
    report = await runner.run(dataset)

    # ── 5. View results ───────────────────────────────────────────────────
    print(report.summary())

    # Show failed cases for debugging
    failed = report.filter_failed()
    if failed:
        print(f"\n--- Failed Cases ({len(failed)}) ---")
        for r in failed:
            print(f"  Input: {r.input[:60]}")
            print(f"  Expected: {r.expected_output}")
            print(f"  Actual: {r.actual_output[:80]}")
            for s in r.scores:
                if not s.passed:
                    print(f"  [{s.criterion}] score={s.score:.2f}: {s.reasoning[:80]}")
            print()

    # ── 6. Export results ─────────────────────────────────────────────────
    EvalRunner.export_json(report, "results/eval_report.json")
    EvalRunner.export_markdown(report, "results/eval_report.md")

    # Show cost tracking
    print(f"\nCost tracking: {cost_tracker.stats}")


if __name__ == "__main__":
    asyncio.run(main())
