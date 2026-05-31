from __future__ import annotations

import json
import logging
import re
from typing import Any

from ravi.agents.guardrails._contracts import (
    GuardrailContext,
    GuardrailResult,
    GuardrailType,
    _fail,
    _pass,
)

logger = logging.getLogger(__name__)


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

        logger.debug("[LLMJudge] checking %r (agent=%s)", text[:80], ctx.agent_name)

        try:
            from ravi.kernel import ChatMessage, TextBlock

            # Wrap as a classification request so the model classifies rather than answers.
            classify_request = f'Classify this message:\n"""\n{text}\n"""'
            messages = [ChatMessage(role="user", content=[TextBlock(text=classify_request)])]
            response = await self._model_client.generate(
                messages,
                system=self._judge_prompt,
            )
            response_text = " ".join(
                b.text for b in response if hasattr(b, "text")
            )
            logger.debug("[LLMJudge] raw response: %r", response_text[:200])
            judgment = self._parse_judgment(response_text)
            safe = judgment.get("safe", True)
            reason = judgment.get("reason", "")
            logger.info(
                "[LLMJudge] verdict=%-5s  reason=%r  input=%r",
                "SAFE" if safe else "BLOCK",
                reason[:100],
                text[:60],
            )

            if not safe:
                return _fail(
                    self.name,
                    self.guardrail_type,
                    f"LLM judge flagged as unsafe: {reason or 'no reason'}",
                    tripwire=self.tripwire,
                    judge_response=judgment,
                )
            return _pass(
                self.name,
                self.guardrail_type,
                f"LLM judge passed: {reason or 'content is safe'}",
                judge_response=judgment,
            )
        except Exception as e:
            logger.warning(
                "[LLMJudge] error — failing open: %s: %s",
                type(e).__name__,
                e,
                exc_info=True,
            )
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
