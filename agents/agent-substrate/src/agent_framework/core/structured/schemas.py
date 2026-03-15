"""Pre-built Pydantic schemas for common structured output use-cases.

All schemas follow OpenAI Structured Outputs constraints:
  - ``model_config = ConfigDict(strict=True)`` for strict validation
  - All fields are ``required`` (no Optional with missing default)
  - ``additionalProperties: false`` is enforced by Pydantic v2's JSON
    schema generation when ``strict=True``

Import any of these directly as the ``schema`` argument to
``parse()``, ``generate_structured()``, or ``LLMJudge``:

    from agent_framework.core.structured.schemas import ContentSafetyJudge
"""
from __future__ import annotations

from typing import Generic, List, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


# ---------------------------------------------------------------------------
# Judge schemas
# ---------------------------------------------------------------------------

class ContentSafetyJudge(BaseModel):
    """LLM-as-judge output schema for content safety moderation.

    Usage::

        judge = LLMJudge(
            client=openai_client,
            schema=ContentSafetyJudge,
            system_prompt='Evaluate if the following content is safe ...',
            pass_field='safe',
        )
    """

    model_config = ConfigDict(strict=True)

    safe: bool = Field(
        description="True if the content is safe; False if it violates any policy."
    )
    reasoning: str = Field(
        description="Step-by-step reasoning that led to the safety determination."
    )
    violated_categories: List[str] = Field(
        description=(
            "List of violated content categories (e.g. 'violence', 'hate_speech'). "
            "Empty list when safe=True."
        )
    )


class RelevanceJudge(BaseModel):
    """LLM-as-judge output schema for answer relevance evaluation.

    Usage::

        judge = LLMJudge(
            client=openai_client,
            schema=RelevanceJudge,
            system_prompt='Evaluate whether the answer is relevant to the question ...',
            pass_field='relevant',
        )
    """

    model_config = ConfigDict(strict=True)

    relevant: bool = Field(
        description="True if the answer is relevant to the input question or context."
    )
    score: float = Field(
        ge=0.0,
        le=1.0,
        description="Relevance confidence score between 0.0 (not relevant) and 1.0 (highly relevant).",
    )
    reasoning: str = Field(
        description="Explanation of why the answer is or is not relevant."
    )


class ClassificationResult(BaseModel):
    """Generic text classification output schema.

    Usage::

        result = await parse(
            client=openai_client,
            messages=[UserMessage(content=[{'type': 'text', 'text': text}])],
            schema=ClassificationResult,
            system='Classify the sentiment of the text. Labels: positive, negative, neutral.',
        )
        print(result.parsed.label, result.parsed.confidence)
    """

    model_config = ConfigDict(strict=True)

    label: str = Field(
        description="The predicted class label (e.g. 'positive', 'negative', 'urgent', etc.)."
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Confidence in the predicted label, between 0.0 and 1.0.",
    )
    reasoning: str = Field(
        description="Brief reasoning for the classification decision."
    )


# ---------------------------------------------------------------------------
# Extraction schema
# ---------------------------------------------------------------------------

class ExtractionResult(BaseModel, Generic[T]):
    """Generic structured data extraction wrapper.

    Use when you want to extract a structured ``data`` payload from
    unstructured text but also want a free-text ``extraction_notes`` field
    for the model to flag edge cases or ambiguities.

    Example::

        class Address(BaseModel):
            street: str
            city: str
            postcode: str

        result = await parse(
            client=openai_client,
            messages=[UserMessage(content=[{'type': 'text', 'text': raw_text}])],
            schema=ExtractionResult[Address],   # generic specialisation
            system='Extract the postal address from the text.',
        )
        address = result.parsed.data
    """

    model_config = ConfigDict(strict=True)

    data: T = Field(description="The extracted structured data.")
    extraction_notes: str = Field(
        description=(
            "Notes from the model about the extraction — flag ambiguities, "
            "missing fields, or confidence caveats here."
        )
    )
