"""LLM-as-Judge evaluator.

Uses an LLM to score agent outputs against defined criteria.
The judge LLM is typically a stronger model (e.g. GPT-4o) evaluating
the output of the agent's model.

Design decisions:
  - Reuses the framework's own BaseModelClient, so any supported model
    can be a judge.
  - Scores are parsed from structured JSON output.
  - Falls back gracefully if the judge LLM returns malformed output.
  - Supports parallel judging of multiple criteria.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import List, Optional

from agent_framework.evals.criteria import EvalCriterion
from agent_framework.evals.models import EvalScore
from agent_framework.model_clients.base_client import BaseModelClient
from agent_framework.messages.client_messages import SystemMessage, UserMessage, AssistantMessage

logger = logging.getLogger("agent_framework.evals")


class LLMJudge:
    """Uses an LLM to evaluate agent outputs against criteria.

    Usage::

        judge = LLMJudge(
            model_client=openai_client,
            criteria=[CORRECTNESS, HELPFULNESS],
        )
        scores = await judge.score(
            input_text="What is 2+2?",
            actual_output="4",
            expected_output="4",
        )
    """

    def __init__(
        self,
        model_client: BaseModelClient,
        criteria: List[EvalCriterion],
        *,
        parallel: bool = True,
        max_retries: int = 2,
    ):
        """
        Args:
            model_client: The LLM client to use for judging.
            criteria: List of criteria to evaluate against.
            parallel: Whether to evaluate criteria in parallel.
            max_retries: Retries if the judge LLM returns unparseable output.
        """
        self.model_client = model_client
        self.criteria = criteria
        self.parallel = parallel
        self.max_retries = max_retries

    async def score(
        self,
        *,
        input_text: str,
        actual_output: str,
        expected_output: Optional[str] = None,
        context: Optional[str] = None,
    ) -> List[EvalScore]:
        """Score an agent output against all configured criteria.

        Returns:
            List of EvalScore, one per criterion.
        """
        if self.parallel:
            tasks = [
                self._score_criterion(
                    criterion, input_text, actual_output,
                    expected_output, context,
                )
                for criterion in self.criteria
            ]
            return await asyncio.gather(*tasks)
        else:
            results = []
            for criterion in self.criteria:
                score = await self._score_criterion(
                    criterion, input_text, actual_output,
                    expected_output, context,
                )
                results.append(score)
            return results

    async def _score_criterion(
        self,
        criterion: EvalCriterion,
        input_text: str,
        actual_output: str,
        expected_output: Optional[str],
        context: Optional[str],
    ) -> EvalScore:
        """Score a single criterion using the judge LLM."""
        # Build context section
        context_section = ""
        if context:
            context_section = f"CONTEXT: {context}"

        # Fill prompt template
        prompt = criterion.prompt_template.format(
            input=input_text,
            actual_output=actual_output,
            expected_output=expected_output or "(not provided)",
            context_section=context_section,
        )

        # Call judge LLM with retries
        for attempt in range(self.max_retries + 1):
            try:
                response = await self.model_client.generate(
                    messages=[
                        SystemMessage(content="You are a precise evaluation judge. Always respond with valid JSON only."),
                        UserMessage(content=[prompt]),
                    ],
                    tools=None,
                )

                # Parse response
                response_text = self._extract_text(response)
                parsed = self._parse_judge_response(response_text)

                raw_score = parsed["score"]
                reasoning = parsed.get("reasoning", "")

                # Normalise to 0.0–1.0
                min_score, max_score = criterion.score_range
                normalised = (raw_score - min_score) / (max_score - min_score)
                normalised = max(0.0, min(1.0, normalised))

                return EvalScore(
                    criterion=criterion.name,
                    score=normalised,
                    passed=normalised >= criterion.threshold,
                    reasoning=reasoning,
                    raw_score=float(raw_score),
                    threshold=criterion.threshold,
                )

            except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
                if attempt < self.max_retries:
                    logger.warning(
                        f"Judge parse failed for '{criterion.name}' "
                        f"(attempt {attempt + 1}/{self.max_retries + 1}): {e}"
                    )
                    continue

                # Final attempt failed — return error score
                logger.error(
                    f"Judge failed for '{criterion.name}' after "
                    f"{self.max_retries + 1} attempts: {e}"
                )
                return EvalScore(
                    criterion=criterion.name,
                    score=0.0,
                    passed=False,
                    reasoning=f"Judge error: {e}",
                    threshold=criterion.threshold,
                )

            except Exception as e:
                logger.error(f"Judge LLM error for '{criterion.name}': {e}")
                return EvalScore(
                    criterion=criterion.name,
                    score=0.0,
                    passed=False,
                    reasoning=f"LLM error: {e}",
                    threshold=criterion.threshold,
                )

        # Should not reach here, but safety fallback
        return EvalScore(
            criterion=criterion.name,
            score=0.0,
            passed=False,
            reasoning="Judge exhausted retries",
            threshold=criterion.threshold,
        )

    @staticmethod
    def _parse_judge_response(text: str) -> dict:
        """Extract JSON from judge response, handling markdown fences."""
        # Strip markdown code fences if present
        cleaned = text.strip()
        if cleaned.startswith("```"):
            # Remove opening fence (```json or ```)
            cleaned = re.sub(r"^```(?:json)?\s*\n?", "", cleaned)
            cleaned = re.sub(r"\n?```\s*$", "", cleaned)

        # Try direct parse
        try:
            result = json.loads(cleaned)
            if "score" in result:
                return result
        except json.JSONDecodeError:
            pass

        # Fallback: find first JSON object in text
        json_match = re.search(r'\{[^{}]*"score"\s*:\s*\d[^{}]*\}', cleaned)
        if json_match:
            return json.loads(json_match.group())

        raise json.JSONDecodeError("No valid JSON with 'score' found", cleaned, 0)

    @staticmethod
    def _extract_text(response: AssistantMessage) -> str:
        """Extract plain text from an AssistantMessage."""
        if response.content is None:
            return ""
        if isinstance(response.content, list):
            parts = [str(c) for c in response.content if c]
            return " ".join(parts)
        return str(response.content)
