"""Tests for PipelineEngine — declarative adapter step execution."""

from __future__ import annotations

import pytest

from raavan.catalog._pipeline import (
    PipelineDef,
    PipelineEngine,
    PipelineStep,
)
from raavan.core.tools.base_tool import BaseTool, ToolResult
from raavan.core.tools.catalog import CapabilityRegistry


class _DoubleTool(BaseTool):
    """Doubles a number for testing."""

    def __init__(self) -> None:
        super().__init__(
            name="doubler",
            description="Double a number",
            input_schema={
                "type": "object",
                "properties": {"value": {"type": "number"}},
                "required": ["value"],
            },
        )

    async def execute(self, value: float = 0, **kwargs) -> ToolResult:
        return ToolResult(content=[{"type": "text", "text": str(value * 2)}])


class _GreetTool(BaseTool):
    """Greeting tool for testing."""

    def __init__(self) -> None:
        super().__init__(
            name="greeter",
            description="Greet someone",
            input_schema={
                "type": "object",
                "properties": {"name": {"type": "string"}},
            },
        )

    async def execute(self, name: str = "World", **kwargs) -> ToolResult:
        return ToolResult(content=[{"type": "text", "text": f"Hello, {name}!"}])


class _FailTool(BaseTool):
    """Always fails for testing error handling."""

    def __init__(self) -> None:
        super().__init__(
            name="fail_tool",
            description="Always fails",
            input_schema={"type": "object", "properties": {}},
        )

    async def execute(self, **kwargs) -> ToolResult:
        return ToolResult(
            content=[{"type": "text", "text": "Something went wrong"}], is_error=True
        )


@pytest.fixture
def catalog() -> CapabilityRegistry:
    cat = CapabilityRegistry()
    cat.register_tool(_DoubleTool(), category="test", tags=["math"])
    cat.register_tool(_GreetTool(), category="test", tags=["text"])
    cat.register_tool(_FailTool(), category="test", tags=["error"])
    return cat


@pytest.fixture
def engine(catalog: CapabilityRegistry) -> PipelineEngine:
    return PipelineEngine(catalog=catalog)


class TestPipelineDef:
    """PipelineDef serialisation tests."""

    def test_to_dict_roundtrip(self) -> None:
        pipeline = PipelineDef(
            name="test-pipe",
            description="A test pipeline",
            steps=[
                PipelineStep(adapter_name="doubler", input_mapping={"value": 5}),
                PipelineStep(
                    adapter_name="greeter",
                    input_mapping={"name": "$prev.content"},
                    output_key="greeting",
                ),
            ],
        )
        d = pipeline.to_dict()
        restored = PipelineDef.from_dict(d)
        assert restored.name == "test-pipe"
        assert len(restored.steps) == 2
        assert restored.steps[0].adapter_name == "doubler"
        assert restored.steps[1].input_mapping["name"] == "$prev.content"


class TestPipelineEngine:
    """PipelineEngine execution tests."""

    async def test_single_step(self, engine: PipelineEngine) -> None:
        pipeline = PipelineDef(
            name="single",
            steps=[PipelineStep(adapter_name="doubler", input_mapping={"value": 7})],
        )
        result = await engine.execute(pipeline)
        assert result.success is True
        assert len(result.step_results) == 1
        assert "14" in str(result.step_results[0]["content"])

    async def test_multi_step_with_prev_ref(self, engine: PipelineEngine) -> None:
        pipeline = PipelineDef(
            name="multi",
            steps=[
                PipelineStep(adapter_name="greeter", input_mapping={"name": "World"}),
                PipelineStep(
                    adapter_name="greeter",
                    input_mapping={"name": "$prev.content"},
                ),
            ],
        )
        result = await engine.execute(pipeline)
        assert result.success is True
        assert len(result.step_results) == 2
        # Second step greets the output of the first
        assert "Hello, World!" in str(result.step_results[1]["content"])

    async def test_missing_adapter_fails(self, engine: PipelineEngine) -> None:
        pipeline = PipelineDef(
            name="bad",
            steps=[PipelineStep(adapter_name="nonexistent")],
        )
        result = await engine.execute(pipeline)
        assert result.success is False
        assert "not found" in (result.error or "")

    async def test_error_step_stops_pipeline(self, engine: PipelineEngine) -> None:
        pipeline = PipelineDef(
            name="error",
            steps=[
                PipelineStep(adapter_name="greeter", input_mapping={"name": "ok"}),
                PipelineStep(adapter_name="fail_tool"),
                PipelineStep(adapter_name="greeter", input_mapping={"name": "never"}),
            ],
        )
        result = await engine.execute(pipeline)
        assert result.success is False
        assert len(result.step_results) == 2  # stopped after fail_tool

    async def test_duration_tracked(self, engine: PipelineEngine) -> None:
        pipeline = PipelineDef(
            name="timed",
            steps=[PipelineStep(adapter_name="doubler", input_mapping={"value": 1})],
        )
        result = await engine.execute(pipeline)
        assert result.duration_ms >= 0


class TestPipelineValidation:
    """PipelineEngine.validate() tests."""

    def test_valid_pipeline(self, engine: PipelineEngine) -> None:
        pipeline = PipelineDef(
            name="valid",
            steps=[PipelineStep(adapter_name="doubler")],
        )
        errors = engine.validate(pipeline)
        assert errors == []

    def test_missing_adapter_name(self, engine: PipelineEngine) -> None:
        pipeline = PipelineDef(
            name="empty",
            steps=[PipelineStep(adapter_name="")],
        )
        errors = engine.validate(pipeline)
        assert len(errors) == 1
        assert "missing adapter_name" in errors[0]

    def test_unknown_adapter(self, engine: PipelineEngine) -> None:
        pipeline = PipelineDef(
            name="unknown",
            steps=[PipelineStep(adapter_name="does_not_exist")],
        )
        errors = engine.validate(pipeline)
        assert len(errors) == 1
        assert "not found" in errors[0]

    def test_empty_pipeline_valid(self, engine: PipelineEngine) -> None:
        pipeline = PipelineDef(name="empty", steps=[])
        errors = engine.validate(pipeline)
        assert errors == []
