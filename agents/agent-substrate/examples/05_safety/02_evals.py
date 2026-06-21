"""05_safety/02_evals.py — LLM-as-judge evaluation and structured output validation.

Demonstrates:
  1. parse()    — extract typed structured data from free-form text (no agent needed)
  2. LLMJudge  — score a single agent response using an LLM judge guardrail
  3. Eval loop — batch scoring of Q&A pairs, compute average quality score
  4. Schema mismatch — what happens when the model output doesn't fit the schema

# Infrastructure: OPENAI_API_KEY environment variable must be set.
"""

import asyncio
import os
import statistics
from typing import List, Optional

from pydantic import BaseModel, Field

from agent_substratereasoning.structured import LLMJudge, parse
from agent_substrate.integrations.llm.openai.openai_client import OpenAIClient
from agent_substrate.kernel.guardrails.base_guardrail import GuardrailContext, GuardrailType
from agent_substrate.kernel.messages.client_messages import UserMessage
from agent_substrate.kernel.messages.content import TextBlock


# ---
# Shared client
# ---


def _client(model: str = "gpt-4o-mini") -> OpenAIClient:
    api_key = os.environ.get("OPENAI_API_KEY", "")
    return OpenAIClient(model=model, api_key=api_key)


# ---
# Inline Pydantic schemas
# ---


class InvoiceLineItem(BaseModel):
    description: str
    quantity: int
    unit_price: float


class Invoice(BaseModel):
    vendor: str
    total: float
    currency: str = "USD"
    line_items: List[InvoiceLineItem] = Field(default_factory=list)


class QualityJudge(BaseModel):
    """Schema used by LLMJudge to score a response."""

    good: bool = Field(
        description="True if the response is correct, helpful, and safe."
    )
    score: float = Field(description="Quality score from 0.0 (worst) to 1.0 (best).")
    reasoning: str = Field(description="Brief justification for the score.")


# ---
# 1. Structured parse — extract Invoice from free-form text
# ---


async def demo_structured_parse() -> None:
    print("\n=== 1. Structured parse — Invoice extraction ===")

    client = _client()
    invoice_text = (
        "Invoice from Acme Corp. "
        "2x Widget Pro @ $49.99 each, 1x Shipping @ $9.99. "
        "Total: $109.97 USD."
    )

    result = await parse(
        client=client,
        messages=[UserMessage(content=[TextBlock(type="text", text=invoice_text)])],
        schema=Invoice,
        system="Extract the invoice details. Return structured JSON.",
    )

    if result.ok:
        inv = result.parsed
        print(f"  Vendor : {inv.vendor}")
        print(f"  Total  : {inv.total} {inv.currency}")
        for item in inv.line_items:
            print(
                f"  Line   : {item.quantity}x {item.description} @ ${item.unit_price}"
            )
    elif result.refused:
        print(f"  Model refused: {result.refusal}")
    else:
        print("  Parse failed (no parsed value).")


# ---
# 2. LLMJudge — score a single agent response
# ---


async def demo_llm_judge() -> None:
    print("\n=== 2. LLMJudge — single response scoring ===")

    judge = LLMJudge(
        client=_client(),
        schema=QualityJudge,
        system_prompt=(
            "You are a strict quality evaluator. "
            "Given an agent's response, decide if it is good (correct, helpful, safe) "
            "and assign a score between 0.0 and 1.0."
        ),
        guardrail_type=GuardrailType.OUTPUT,
        pass_field="good",
        name="quality_judge",
    )

    for question, answer in [
        ("What is 2 + 2?", "The answer is 4."),
        ("What is the capital of France?", "I don't know, maybe London?"),
        (
            "Summarise the water cycle.",
            "Water evaporates, forms clouds, and falls as rain.",
        ),
    ]:
        ctx = GuardrailContext(
            agent_name="demo",
            run_id="ex-02",
            output_text=f"Q: {question}\nA: {answer}",
        )
        result = await judge.check(ctx)
        score = result.metadata.get("score", "n/a")
        print(f"  Q: {question!r:.55s}")
        print(f"  A: {answer!r:.55s}")
        print(
            f"  passed={result.passed}  score={score}  reason={result.message[:80]!r}"
        )
        print()


# ---
# 3. Evaluation loop — batch scoring, average score
# ---


QA_PAIRS = [
    ("What is the boiling point of water in Celsius?", "100 degrees Celsius."),
    ("Who wrote Hamlet?", "William Shakespeare."),
    ("What is 7 * 8?", "56."),
    ("What is the speed of light?", "Roughly 3 times 10 to the 8th metres per second."),
    ("Name a planet in our solar system.", "The sun."),  # intentionally wrong
]


async def demo_eval_loop() -> None:
    print("\n=== 3. Evaluation loop — batch scoring ===")

    judge = LLMJudge(
        client=_client(),
        schema=QualityJudge,
        system_prompt=(
            "You are an accuracy evaluator. "
            "Rate whether the answer correctly and helpfully responds to the question. "
            "Return good=True only for factually correct, complete answers."
        ),
        guardrail_type=GuardrailType.OUTPUT,
        pass_field="good",
        name="eval_judge",
    )

    scores: List[float] = []
    for question, answer in QA_PAIRS:
        ctx = GuardrailContext(
            agent_name="eval",
            run_id="eval-loop",
            output_text=f"Question: {question}\nAnswer: {answer}",
        )
        result = await judge.check(ctx)
        score = float(result.metadata.get("score", 1.0 if result.passed else 0.0))
        scores.append(score)
        passed_str = "PASS" if result.passed else "FAIL"
        print(f"  [{passed_str}] score={score:.2f}  Q: {question!r:.50s}")

    avg = statistics.mean(scores) if scores else 0.0
    print(f"\n  Average score: {avg:.3f} over {len(scores)} cases")


# ---
# 4. Schema mismatch — missing required field
# ---


class IncompleteSchema(BaseModel):
    """Schema intentionally missing the 'good' bool field expected by pass_field."""

    notes: str
    confidence: Optional[float] = None


async def demo_schema_mismatch() -> None:
    print("\n=== 4. Schema mismatch — wrong pass_field ===")

    judge = LLMJudge(
        client=_client(),
        schema=IncompleteSchema,
        system_prompt="Evaluate the text and return notes.",
        guardrail_type=GuardrailType.OUTPUT,
        pass_field="good",  # 'good' does not exist on IncompleteSchema
        name="misconfigured_judge",
    )

    ctx = GuardrailContext(
        agent_name="demo",
        run_id="mismatch",
        output_text="The Eiffel Tower is in Paris.",
    )
    result = await judge.check(ctx)
    print(f"  passed={result.passed}  tripwire={result.tripwire}")
    print(f"  message={result.message!r}")


# ---
# Entry point
# ---


async def main() -> None:
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY is not set — skipping all LLM-dependent demos.")
        print("Export OPENAI_API_KEY and re-run to see the full output.")
        return

    await demo_structured_parse()
    await demo_llm_judge()
    await demo_eval_loop()
    await demo_schema_mismatch()
    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
