"""LLMJudge — a ``BaseGuardrail`` that uses structured outputs to evaluate
content through an LLM with a typed decision schema.

``LLMJudge`` is the primary way to build LLM-as-judge patterns in the
framework.  Because it subclasses ``BaseGuardrail``, it can be plugged
directly into ``input_guardrails`` or ``output_guardrails`` on any agent:

    agent = ReActAgent(
        ...
        output_guardrails=[
            LLMJudge(
                client=openai_client,
                schema=ContentSafetyJudge,
                system_prompt='You are a content safety classifier ...',
                guardrail_type=GuardrailType.OUTPUT,
                pass_field='safe',
            )
        ],
    )

Design notes:
  - Never raises — all errors surface as failed ``GuardrailResult`` so the
    agent loop can decide what to do (soft vs hard failure is still the
    agent's choice via ``tripwire``).
  - ``pass_field`` is the name of a ``bool`` field on the schema whose
    value determines passed/failed.
  - ``tripwire_on_refusal`` (default True) causes a hard stop when the
    LLM refuses to evaluate (safety refusal of the judge itself).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Type, TYPE_CHECKING

from pydantic import BaseModel

from agent_framework.core.guardrails.base_guardrail import (
    BaseGuardrail,
    GuardrailContext,
    GuardrailResult,
    GuardrailType,
)
from agent_framework.core.messages.client_messages import SystemMessage, UserMessage

if TYPE_CHECKING:
    from agent_framework.integrations.llm.base_client import BaseModelClient

logger = logging.getLogger("agent_framework.structured.judge")


class LLMJudge(BaseGuardrail):
    """An LLM-as-judge guardrail backed by structured outputs.

    The judge makes one ``generate_structured()`` call per evaluation and
    reads ``result.parsed.{pass_field}`` (must be ``bool``) to determine
    the guardrail outcome.

    Args:
        client: Any ``BaseModelClient`` implementing ``generate_structured``.
        schema: A Pydantic BaseModel subclass with at least one ``bool``
            field whose name matches ``pass_field``.
        system_prompt: Instructions to the judge model.  Include any
            rubric, examples, or policy text here.
        name: Name for audit logs.  Defaults to the schema class name.
        guardrail_type: ``INPUT`` or ``OUTPUT`` (default ``OUTPUT``).
        pass_field: Name of the boolean field on ``schema`` to use as the
            pass/fail signal (default ``'safe'``).
        tripwire_on_fail: If ``True``, a failed judge causes a hard stop
            (tripwire) rather than a soft failure.  Default ``False``.
        tripwire_on_refusal: If ``True``, a model refusal from the judge
            itself counts as a tripwire.  Default ``True``.
        description: Optional description for this guardrail instance.

    Example::

        from agent_framework.core.structured import LLMJudge, ContentSafetyJudge

        judge = LLMJudge(
            client=openai_client,
            schema=ContentSafetyJudge,
            system_prompt=(
                'You are a content safety evaluator. '
                'Determine if the following text is safe for all audiences.'
            ),
            pass_field='safe',
            tripwire_on_fail=True,
        )
    """

    # Class-level name is required by BaseGuardrail.__init_subclass__
    name = "llm_judge"

    def __init__(
        self,
        client: "BaseModelClient",
        schema: Type[BaseModel],
        system_prompt: str,
        *,
        name: str = "",
        guardrail_type: GuardrailType = GuardrailType.OUTPUT,
        pass_field: str = "safe",
        tripwire_on_fail: bool = False,
        tripwire_on_refusal: bool = True,
        description: str = "",
    ) -> None:
        # Override class-level name if caller supplies one
        self.name = name or f"llm_judge:{schema.__name__}"
        self.description = description or f"LLM judge using {schema.__name__} schema."
        self.guardrail_type = guardrail_type

        self._client = client
        self._schema = schema
        self._system_prompt = system_prompt
        self._pass_field = pass_field
        self._tripwire_on_fail = tripwire_on_fail
        self._tripwire_on_refusal = tripwire_on_refusal

    async def check(self, ctx: GuardrailContext) -> GuardrailResult:
        """Run the LLM judge and return a typed guardrail result.

        Picks the relevant text from ``ctx`` based on ``guardrail_type``,
        sends it to the judge model, and maps the structured output to a
        ``GuardrailResult``.
        """
        text_to_judge = (
            ctx.input_text
            if self.guardrail_type == GuardrailType.INPUT
            else ctx.output_text
        )

        if not text_to_judge:
            # Nothing to judge — pass through
            return self._pass(message="No text to evaluate; skipping judge.")

        try:
            messages = [
                SystemMessage(content=self._system_prompt),
                UserMessage(content=[{"type": "text", "text": text_to_judge}]),
            ]
            result = await self._client.generate_structured(messages, self._schema)
        except Exception as exc:
            logger.error(
                "[%s] generate_structured failed: %s", self.name, exc, exc_info=True
            )
            return GuardrailResult(
                guardrail_name=self.name,
                guardrail_type=self.guardrail_type,
                passed=False,
                tripwire=False,
                message=f"Judge error: {exc}",
                metadata={"error": str(exc)},
            )

        # Safety refusal from the judge itself
        if result.refused:
            logger.warning("[%s] Judge model refused: %s", self.name, result.refusal)
            return GuardrailResult(
                guardrail_name=self.name,
                guardrail_type=self.guardrail_type,
                passed=False,
                tripwire=self._tripwire_on_refusal,
                message=f"Judge refused: {result.refusal}",
                metadata={"refusal": result.refusal},
            )

        # Read the pass/fail boolean from the parsed schema
        parsed = result.parsed
        try:
            passed: bool = bool(getattr(parsed, self._pass_field))
        except AttributeError:
            logger.error(
                "[%s] Schema '%s' has no field '%s'.",
                self.name,
                self._schema.__name__,
                self._pass_field,
            )
            return GuardrailResult(
                guardrail_name=self.name,
                guardrail_type=self.guardrail_type,
                passed=False,
                tripwire=False,
                message=(
                    f"Judge misconfigured: schema '{self._schema.__name__}' "
                    f"has no field '{self._pass_field}'."
                ),
            )

        # Build reasoning message (look for a 'reasoning' field by convention)
        reasoning = getattr(parsed, "reasoning", "") or ""

        # Build metadata dict from all parsed fields
        metadata: Dict[str, Any] = {}
        if parsed is not None:
            try:
                metadata = parsed.model_dump()
            except Exception:
                pass

        return GuardrailResult(
            guardrail_name=self.name,
            guardrail_type=self.guardrail_type,
            passed=passed,
            tripwire=self._tripwire_on_fail and not passed,
            message=reasoning,
            metadata=metadata,
        )
