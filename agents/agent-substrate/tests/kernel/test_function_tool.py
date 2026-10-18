"""``@tool``: a typed function becomes a ``Tool`` — schema from the signature, docs from the docstring, and both
engine-facing declarations (``risk``, ``idempotent``) required."""

from __future__ import annotations

from enum import Enum
from typing import Literal

import pytest
from pydantic import BaseModel

from substrate.tools import Tool, ToolExecutionResult, ToolRisk, tool
from substrate.types import TextBlock


@tool(risk=ToolRisk.SAFE, idempotent=True)
async def word_count(text: str, ignore_case: bool = False) -> int:
    """Count the words in a piece of text.

    Longer explanation that is not the summary.

    Args:
        text: The text to count.
        ignore_case: Treat upper and lower case alike.
    """
    return len(text.split())


async def test_the_schema_comes_from_the_signature_and_the_docs_from_the_docstring() -> (
    None
):
    assert word_count.name == "word_count"
    assert word_count.description == "Count the words in a piece of text."
    assert word_count.input_schema == {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "The text to count."},
            "ignore_case": {
                "type": "boolean",
                "default": False,
                "description": "Treat upper and lower case alike.",
            },
        },
        "required": ["text"],
        "additionalProperties": False,
    }
    assert isinstance(word_count, Tool)  # the engine's own contract


async def test_calling_it_runs_the_function_with_validated_arguments() -> None:
    result = await word_count.execute(text="to be or not to be")
    assert (
        isinstance(result, ToolExecutionResult)
        and result.text == "6"
        and result.name == "word_count"
    )
    assert (
        await word_count("a b c") == 3
    )  # still plain code: calling the tool calls the function


async def test_bad_arguments_come_back_as_an_error_the_model_can_read_not_a_crash() -> (
    None
):
    result = await word_count.execute(text=["not", "text"])
    assert result.is_error and "text" in result.text
    result = await word_count.execute(text="x", surprise=1)
    assert result.is_error and "surprise" in result.text


def test_risk_and_idempotent_are_required() -> None:
    with pytest.raises(TypeError):
        tool(risk=ToolRisk.SAFE)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        tool(idempotent=True)  # type: ignore[call-arg]


def test_a_tool_needs_a_description_the_model_can_read() -> None:
    with pytest.raises(ValueError, match="description"):

        @tool(risk=ToolRisk.SAFE, idempotent=True)
        def nameless(x: int) -> int:
            return x


def test_variadic_parameters_cannot_be_described_to_a_model() -> None:
    with pytest.raises(TypeError, match="kwargs"):

        @tool(risk=ToolRisk.SAFE, idempotent=True)
        def anything(**kwargs: int) -> int:
            """Takes anything."""
            return 0


class Colour(Enum):
    RED = "red"
    BLUE = "blue"


class Point(BaseModel):
    x: int
    y: int


async def test_rich_parameter_types_are_described_and_coerced() -> None:
    @tool(risk=ToolRisk.SAFE, idempotent=True)
    def paint(
        colour: Colour,
        where: Point,
        mode: Literal["fill", "outline"] = "fill",
        tags: list[str] | None = None,
    ) -> dict:
        """Paint a point."""
        return {"colour": colour.value, "x": where.x, "mode": mode, "tags": tags}

    properties = paint.input_schema["properties"]
    assert (
        properties["mode"]["enum"] == ["fill", "outline"]
        and "$ref" in properties["colour"]
    )
    assert set(paint.input_schema["required"]) == {"colour", "where"}
    result = await paint.execute(colour="red", where={"x": 3, "y": 4}, tags=["a"])
    assert result.text == '{"colour": "red", "x": 3, "mode": "fill", "tags": ["a"]}'


async def test_results_may_be_text_numbers_json_blocks_or_a_full_result() -> None:
    @tool(risk=ToolRisk.SAFE, idempotent=True)
    def shapes(kind: str):
        """Return different shapes."""
        return {
            "text": "plain",
            "none": None,
            "float": 1.5,
            "block": TextBlock(text="block"),
            "blocks": [TextBlock(text="a"), TextBlock(text="b")],
            "full": ToolExecutionResult(
                content=[TextBlock(text="full")], metadata={"k": 1}
            ),
        }[kind]

    assert (await shapes.execute(kind="text")).text == "plain"
    assert (await shapes.execute(kind="none")).text == ""
    assert (await shapes.execute(kind="float")).text == "1.5"
    assert (await shapes.execute(kind="block")).text == "block"
    assert [b.text for b in (await shapes.execute(kind="blocks")).content] == ["a", "b"]
    full = await shapes.execute(kind="full")
    assert full.name == "shapes" and full.metadata == {"k": 1}


async def test_ctx_is_injected_when_asked_for_and_is_not_part_of_the_schema() -> None:
    seen = []

    @tool(risk=ToolRisk.SAFE, idempotent=True)
    async def with_context(n: int, ctx=None) -> str:
        """Uses the run context."""
        seen.append(ctx)
        return str(n)

    assert "ctx" not in with_context.input_schema["properties"]
    await with_context.execute(ctx="the-context", n=1)
    assert seen == ["the-context"]


async def test_a_synchronous_function_does_not_block_the_event_loop() -> None:
    import asyncio
    import threading

    main = threading.get_ident()

    @tool(risk=ToolRisk.SAFE, idempotent=True)
    def where_am_i() -> str:
        """Report the thread."""
        return "loop" if threading.get_ident() == main else "worker"

    assert (await where_am_i.execute()).text == "worker"
    await asyncio.sleep(0)


def test_name_description_and_concurrency_can_be_set_explicitly() -> None:
    @tool(
        risk=ToolRisk.HIGH,
        idempotent=False,
        name="send",
        description="Send a message.",
        concurrency_safe=True,
    )
    def _impl(to: str) -> str:
        return to

    assert (
        _impl.name,
        _impl.description,
        _impl.risk,
        _impl.idempotent,
        _impl.concurrency_safe,
    ) == (
        "send",
        "Send a message.",
        ToolRisk.HIGH,
        False,
        True,
    )
