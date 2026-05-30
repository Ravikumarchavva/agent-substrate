from __future__ import annotations

import json
import re
from typing import Any

from ravi.reasoning.guardrails._contracts import (
    GuardrailContext,
    GuardrailResult,
    GuardrailType,
    _fail,
    _pass,
)


class LLMJudgeGuardrail:
    """Use a secondary LLM to judge content safety or policy compliance.

    The judge prompt must instruct the model to respond with JSON:
    ``{"safe": bool, "reason": str}``
    """

    name = "llm_judge"
    description = "Uses a secondary LLM to judge content safety"

    _DEFAULT_JUDGE_PROMPT = (
        "You are a content safety classifier. Evaluate if the following content "
        "is safe, appropriate, and free of harmful intent.\n\n"
        'Respond ONLY with a JSON object: {"safe": true/false, "reason": "brief explanation"}\n\n'
        "Content to evaluate:\n"
    )

    def __init__(
        self,
        *,
        model_client: Any,
        judge_prompt: str | None = None,
        guardrail_type: GuardrailType = GuardrailType.INPUT,
        tripwire: bool = True,
    ):
        self.guardrail_type = guardrail_type
        self.tripwire = tripwire
        self._model_client = model_client
        self._judge_prompt = judge_prompt or self._DEFAULT_JUDGE_PROMPT

    async def check(self, ctx: GuardrailContext) -> GuardrailResult:
        text = (
            ctx.input_text if self.guardrail_type == GuardrailType.INPUT else ctx.output_text
        )
        if not text:
            return _pass(self.name, self.guardrail_type, "No text to judge")

        try:
            from ravi.kernel import ChatMessage, TextBlock

            messages = [ChatMessage(role="user", content=[TextBlock(text=text)])]
            response = await self._model_client.generate(
                messages,
                system=self._judge_prompt,
            )
            response_text = " ".join(
                b.text for b in response if hasattr(b, "text")
            )
            judgment = self._parse_judgment(response_text)

            if not judgment.get("safe", True):
                return _fail(
                    self.name,
                    self.guardrail_type,
                    f"LLM judge flagged as unsafe: {judgment.get('reason', 'no reason')}",
                    tripwire=self.tripwire,
                    judge_response=judgment,
                )
            return _pass(
                self.name,
                self.guardrail_type,
                f"LLM judge passed: {judgment.get('reason', 'content is safe')}",
                judge_response=judgment,
            )
        except Exception as e:
            return _pass(
                self.name,
                self.guardrail_type,
                f"LLM judge error (failing open): {e}",
                error=str(e),
            )

    @staticmethod
    def _parse_judgment(text: str) -> dict[str, Any]:
        try:
            return json.loads(text.strip())
        except (json.JSONDecodeError, ValueError):
            pass
        json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except (json.JSONDecodeError, ValueError):
                pass
        json_match = re.search(r"\{[^{}]*\"safe\"[^{}]*\}", text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group())
            except (json.JSONDecodeError, ValueError):
                pass
        lower = text.lower()
        if "unsafe" in lower or "not safe" in lower or '"safe": false' in lower:
            return {"safe": False, "reason": text[:200]}
        return {"safe": True, "reason": text[:200]}
