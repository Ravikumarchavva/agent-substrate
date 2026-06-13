from __future__ import annotations

import json
import re
from typing import Any, Awaitable, Callable

from ravi.agents.middleware._contracts import ChatContext
from ravi.kernel.core.errors import MiddlewareTermination
from ravi.logger import setup_logging

logger = setup_logging()


class LLMJudgeMiddleware:
    """Use a secondary LLM to judge content safety of the primary LLM's output."""

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
    ):
        self._model_client = model_client
        self._judge_prompt = judge_prompt or self._DEFAULT_JUDGE_PROMPT

    async def process(
        self, context: ChatContext, call_next: Callable[[], Awaitable[None]]
    ) -> None:
        await call_next()

        if not context.result:
            return

        text = " ".join(b.text for b in context.result.content if hasattr(b, "text"))
        if not text:
            return

        logger.debug("[LLMJudge] checking %r (agent=%s)", text[:80], context.agent_name)

        try:
            from ravi.kernel import ChatMessage, TextBlock

            classify_request = f'Classify this message:\n"""\n{text}\n"""'
            messages = [
                ChatMessage(role="user", content=[TextBlock(text=classify_request)])
            ]
            from ravi.kernel.llm import GenerationOptions

            resp = await self._model_client.generate(
                messages,
                options=GenerationOptions(system_instructions=self._judge_prompt),
            )
            response_text = " ".join(b.text for b in resp.content if hasattr(b, "text"))
            judgment = self._parse_judgment(response_text)
            safe = judgment.get("safe", True)
            reason = judgment.get("reason", "")

            if not safe:
                raise MiddlewareTermination(
                    f"LLMJudge flagged as unsafe: {reason or 'no reason'}"
                )

        except MiddlewareTermination:
            raise
        except Exception as e:
            logger.warning("[LLMJudge] error — failing open: %s", e)

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
