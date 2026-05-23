from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional

from ravi.core.guardrails.base_guardrail import (
    BaseGuardrail,
    GuardrailContext,
    GuardrailResult,
    GuardrailType,
)


class LLMJudgeGuardrail(BaseGuardrail):
    """Use a secondary LLM to judge content safety or policy compliance.

    The judge prompt must instruct the model to respond with JSON:
    ``{"safe": bool, "reason": str}``

    Args:
        model_client: A BaseModelClient instance for the judge model.
        judge_prompt: System prompt for the judge.
        guardrail_type: INPUT or OUTPUT.
        tripwire: Hard stop when the judge says unsafe.
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
        judge_prompt: Optional[str] = None,
        guardrail_type: GuardrailType = GuardrailType.INPUT,
        tripwire: bool = True,
    ):
        self.guardrail_type = guardrail_type
        self.tripwire = tripwire
        self._model_client = model_client
        self._judge_prompt = judge_prompt or self._DEFAULT_JUDGE_PROMPT

    async def check(self, ctx: GuardrailContext) -> GuardrailResult:
        text = (
            ctx.input_text
            if self.guardrail_type == GuardrailType.INPUT
            else ctx.output_text
        )
        if not text:
            return self._pass("No text to judge")

        try:
            from ravi.core.messages.client_messages import SystemMessage, UserMessage

            messages = [
                SystemMessage(content=self._judge_prompt),
                UserMessage(content=[text]),
            ]
            response = await self._model_client.generate_text(messages=messages)

            response_text = ""
            if response.content:
                response_text = " ".join(
                    str(c) for c in response.content if isinstance(c, str)
                )

            judgment = self._parse_judgment(response_text)

            if not judgment.get("safe", True):
                return self._fail(
                    f"LLM judge flagged as unsafe: {judgment.get('reason', 'no reason')}",
                    tripwire=self.tripwire,
                    judge_response=judgment,
                )

            return self._pass(
                f"LLM judge passed: {judgment.get('reason', 'content is safe')}",
                judge_response=judgment,
            )

        except Exception as e:
            # Guardrails never raise — fail open on judge errors
            return self._pass(
                f"LLM judge error (failing open): {str(e)}",
                error=str(e),
            )

    @staticmethod
    def _parse_judgment(text: str) -> Dict[str, Any]:
        """Extract JSON from potentially markdown-wrapped LLM response."""
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
