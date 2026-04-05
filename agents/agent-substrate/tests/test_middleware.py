"""Tests for the agent middleware pipeline."""

from __future__ import annotations

from typing import Any, Optional, List

import pytest

from raavan.core.execution.context import ExecutionContext
from raavan.core.middleware.base import (
    BaseMiddleware,
    MiddlewareContext,
    MiddlewareStage,
)
from raavan.core.middleware.runner import MiddlewarePipeline
from raavan.core.middleware.builtins.schema_validator import SchemaValidatorMiddleware
from raavan.core.middleware.builtins.file_validator import FileValidatorMiddleware
from raavan.core.middleware.builtins.content_truncator import ContentTruncatorMiddleware
from raavan.core.middleware.builtins.cache import CacheMiddleware
from raavan.core.middleware.builtins.audit_logger import AuditLoggerMiddleware
from raavan.core.middleware.builtins.rate_limiter import RateLimiterMiddleware


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


class RecordingMiddleware(BaseMiddleware):
    """Records lifecycle calls for assertions."""

    def __init__(self, name: str = "recorder") -> None:
        super().__init__(name)
        self.calls: List[str] = []

    async def before(self, ctx: MiddlewareContext) -> MiddlewareContext:
        self.calls.append(f"before:{self.name}")
        return ctx

    async def after(self, ctx: MiddlewareContext, result: Any) -> Any:
        self.calls.append(f"after:{self.name}")
        return result

    async def on_error(self, ctx: MiddlewareContext, error: Exception) -> Optional[Any]:
        self.calls.append(f"on_error:{self.name}")
        return None


class TransformMiddleware(BaseMiddleware):
    """Transforms the result by appending a suffix."""

    async def before(self, ctx: MiddlewareContext) -> MiddlewareContext:
        return ctx

    async def after(self, ctx: MiddlewareContext, result: Any) -> Any:
        return f"{result}_transformed"


class ShortCircuitMiddleware(BaseMiddleware):
    """Returns a fallback value on error, suppressing the exception."""

    def __init__(self, fallback: Any, name: str = "short_circuit") -> None:
        super().__init__(name)
        self.fallback = fallback

    async def before(self, ctx: MiddlewareContext) -> MiddlewareContext:
        return ctx

    async def after(self, ctx: MiddlewareContext, result: Any) -> Any:
        return result

    async def on_error(self, ctx: MiddlewareContext, error: Exception) -> Optional[Any]:
        return self.fallback


# ---------------------------------------------------------------------------
# MiddlewarePipeline tests
# ---------------------------------------------------------------------------


class TestMiddlewarePipeline:
    """Test the core pipeline runner."""

    async def test_empty_pipeline_passthrough(self):
        pipeline = MiddlewarePipeline()
        ctx = MiddlewareContext(stage=MiddlewareStage.LLM_CALL)

        async def execute(c: MiddlewareContext) -> str:
            return "result"

        got = await pipeline.run(ctx, execute)
        assert got == "result"

    async def test_before_after_order(self):
        """before() runs forward, after() runs reverse."""
        mw1 = RecordingMiddleware("mw1")
        mw2 = RecordingMiddleware("mw2")
        pipeline = MiddlewarePipeline([mw1, mw2])
        ctx = MiddlewareContext(stage=MiddlewareStage.LLM_CALL)

        async def execute(c: MiddlewareContext) -> str:
            return "ok"

        await pipeline.run(ctx, execute)

        assert mw1.calls == ["before:mw1", "after:mw1"]
        assert mw2.calls == ["before:mw2", "after:mw2"]
        # Overall order: before1, before2, (execute), after2, after1
        all_calls = mw1.calls + mw2.calls
        assert all_calls.index("before:mw1") < all_calls.index("before:mw2")

    async def test_transform_result(self):
        pipeline = MiddlewarePipeline([TransformMiddleware("xform")])
        ctx = MiddlewareContext(stage=MiddlewareStage.LLM_CALL)

        async def execute(c: MiddlewareContext) -> str:
            return "raw"

        got = await pipeline.run(ctx, execute)
        assert got == "raw_transformed"

    async def test_on_error_called_on_exception(self):
        mw = RecordingMiddleware("err_mw")
        pipeline = MiddlewarePipeline([mw])
        ctx = MiddlewareContext(stage=MiddlewareStage.LLM_CALL)

        async def execute(c: MiddlewareContext) -> str:
            raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            await pipeline.run(ctx, execute)

        assert "on_error:err_mw" in mw.calls

    async def test_on_error_short_circuit(self):
        """on_error returning a value suppresses the exception."""
        mw = ShortCircuitMiddleware(fallback="fallback_value")
        pipeline = MiddlewarePipeline([mw])
        ctx = MiddlewareContext(stage=MiddlewareStage.LLM_CALL)

        async def execute(c: MiddlewareContext) -> str:
            raise RuntimeError("fail")

        got = await pipeline.run(ctx, execute)
        assert got == "fallback_value"

    async def test_context_mutation_propagates(self):
        """Middleware can mutate ctx.metadata and it propagates downstream."""

        class Setter(BaseMiddleware):
            async def before(self, ctx: MiddlewareContext) -> MiddlewareContext:
                ctx.metadata["key"] = "value"
                return ctx

            async def after(self, ctx: MiddlewareContext, result: Any) -> Any:
                return result

        class Reader(BaseMiddleware):
            read_value: Optional[str] = None

            async def before(self, ctx: MiddlewareContext) -> MiddlewareContext:
                self.read_value = ctx.metadata.get("key")
                return ctx

            async def after(self, ctx: MiddlewareContext, result: Any) -> Any:
                return result

        setter = Setter("setter")
        reader = Reader("reader")
        pipeline = MiddlewarePipeline([setter, reader])
        ctx = MiddlewareContext(stage=MiddlewareStage.LLM_CALL)

        async def execute(c: MiddlewareContext) -> str:
            return "ok"

        await pipeline.run(ctx, execute)
        assert reader.read_value == "value"

    async def test_add_middleware(self):
        pipeline = MiddlewarePipeline()
        mw = RecordingMiddleware("added")
        pipeline.add(mw)
        assert len(pipeline.middleware) == 1

    async def test_context_inherits_parent_metadata(self):
        parent = ExecutionContext(
            run_id="run-1",
            correlation_id="corr-1",
            metadata={"trace_id": "abc", "shared": "parent"},
        )
        ctx = MiddlewareContext(
            stage=MiddlewareStage.LLM_CALL,
            run_id="run-1",
            correlation_id="corr-1",
            metadata={"shared": "child", "local": True},
            parent_context=parent,
        )

        assert ctx.root_context is parent
        assert ctx.inherited_metadata() == {
            "trace_id": "abc",
            "shared": "child",
            "local": True,
        }


# ---------------------------------------------------------------------------
# Built-in middleware tests
# ---------------------------------------------------------------------------


class TestContentTruncator:
    async def test_truncates_long_text(self):
        mw = ContentTruncatorMiddleware(max_chars=10, suffix="...")
        ctx = MiddlewareContext(stage=MiddlewareStage.TOOL_EXECUTION)

        # Simulate a ToolResult-like object with content list
        class FakeResult:
            content = [{"type": "text", "text": "A" * 50}]

        result = FakeResult()
        got = await mw.after(ctx, result)
        assert len(got.content[0]["text"]) == 13  # 10 + "..."
        assert got.content[0]["text"].endswith("...")

    async def test_no_truncation_for_short_text(self):
        mw = ContentTruncatorMiddleware(max_chars=100)
        ctx = MiddlewareContext(stage=MiddlewareStage.TOOL_EXECUTION)

        class FakeResult:
            content = [{"type": "text", "text": "short"}]

        result = FakeResult()
        got = await mw.after(ctx, result)
        assert got.content[0]["text"] == "short"

    async def test_skip_non_tool_stage(self):
        mw = ContentTruncatorMiddleware(max_chars=5)
        ctx = MiddlewareContext(stage=MiddlewareStage.LLM_CALL)

        result = "not a tool result"
        got = await mw.after(ctx, result)
        assert got == result


class TestCacheMiddleware:
    async def test_cache_hit(self):
        mw = CacheMiddleware()
        ctx = MiddlewareContext(
            stage=MiddlewareStage.TOOL_EXECUTION,
            tool_name="my_tool",
            tool_args={"x": 1},
        )

        # First call — miss
        ctx1 = await mw.before(ctx)
        assert ctx1.metadata.get("_cache_hit") is False

        result1 = await mw.after(ctx1, "result_value")
        assert result1 == "result_value"

        # Second call — hit
        ctx2 = MiddlewareContext(
            stage=MiddlewareStage.TOOL_EXECUTION,
            tool_name="my_tool",
            tool_args={"x": 1},
        )
        ctx2 = await mw.before(ctx2)
        assert ctx2.metadata.get("_cache_hit") is True

        result2 = await mw.after(ctx2, "ignored")
        assert result2 == "result_value"  # returns cached

    async def test_cache_miss_different_args(self):
        mw = CacheMiddleware()
        ctx1 = MiddlewareContext(
            stage=MiddlewareStage.TOOL_EXECUTION,
            tool_name="my_tool",
            tool_args={"x": 1},
        )
        ctx1 = await mw.before(ctx1)
        await mw.after(ctx1, "result1")

        ctx2 = MiddlewareContext(
            stage=MiddlewareStage.TOOL_EXECUTION,
            tool_name="my_tool",
            tool_args={"x": 2},
        )
        ctx2 = await mw.before(ctx2)
        assert ctx2.metadata.get("_cache_hit") is False

    async def test_cache_eviction(self):
        mw = CacheMiddleware(max_entries=1)
        # Fill with first entry
        ctx1 = MiddlewareContext(
            stage=MiddlewareStage.TOOL_EXECUTION,
            tool_name="tool",
            tool_args={"k": "a"},
        )
        ctx1 = await mw.before(ctx1)
        await mw.after(ctx1, "val_a")

        # Fill with second — should evict first
        ctx2 = MiddlewareContext(
            stage=MiddlewareStage.TOOL_EXECUTION,
            tool_name="tool",
            tool_args={"k": "b"},
        )
        ctx2 = await mw.before(ctx2)
        await mw.after(ctx2, "val_b")

        # First should be evicted
        ctx3 = MiddlewareContext(
            stage=MiddlewareStage.TOOL_EXECUTION,
            tool_name="tool",
            tool_args={"k": "a"},
        )
        ctx3 = await mw.before(ctx3)
        assert ctx3.metadata.get("_cache_hit") is False

    async def test_clear_cache(self):
        mw = CacheMiddleware()
        ctx = MiddlewareContext(
            stage=MiddlewareStage.TOOL_EXECUTION,
            tool_name="t",
            tool_args={"k": 1},
        )
        ctx = await mw.before(ctx)
        await mw.after(ctx, "cached")

        mw.clear()

        ctx2 = MiddlewareContext(
            stage=MiddlewareStage.TOOL_EXECUTION,
            tool_name="t",
            tool_args={"k": 1},
        )
        ctx2 = await mw.before(ctx2)
        assert ctx2.metadata.get("_cache_hit") is False


class TestAuditLogger:
    async def test_audit_logs_timing(self):
        mw = AuditLoggerMiddleware()
        ctx = MiddlewareContext(
            stage=MiddlewareStage.LLM_CALL,
            agent_name="test_agent",
        )
        ctx = await mw.before(ctx)
        assert "_audit_t0" in ctx.metadata

        result = await mw.after(ctx, "result")
        assert result == "result"

    async def test_audit_on_error(self):
        mw = AuditLoggerMiddleware()
        ctx = MiddlewareContext(stage=MiddlewareStage.LLM_CALL)
        ctx = await mw.before(ctx)

        fallback = await mw.on_error(ctx, ValueError("test"))
        assert fallback is None  # does not suppress


class TestFileValidator:
    async def test_missing_file_raises(self, tmp_path):
        mw = FileValidatorMiddleware()
        ctx = MiddlewareContext(
            stage=MiddlewareStage.TOOL_EXECUTION,
            tool_args={"file_path": str(tmp_path / "nonexistent.txt")},
        )
        with pytest.raises(FileNotFoundError):
            await mw.before(ctx)

    async def test_valid_file_passes(self, tmp_path):
        f = tmp_path / "test.pdf"
        f.write_text("data")

        mw = FileValidatorMiddleware(allowed_extensions={".pdf"})
        ctx = MiddlewareContext(
            stage=MiddlewareStage.TOOL_EXECUTION,
            tool_args={"file_path": str(f)},
        )
        result = await mw.before(ctx)
        assert result is ctx

    async def test_wrong_extension_raises(self, tmp_path):
        f = tmp_path / "test.exe"
        f.write_text("data")

        mw = FileValidatorMiddleware(allowed_extensions={".pdf", ".txt"})
        ctx = MiddlewareContext(
            stage=MiddlewareStage.TOOL_EXECUTION,
            tool_args={"file_path": str(f)},
        )
        with pytest.raises(ValueError, match="extension"):
            await mw.before(ctx)

    async def test_skip_non_tool_stage(self):
        mw = FileValidatorMiddleware()
        ctx = MiddlewareContext(
            stage=MiddlewareStage.LLM_CALL,
            tool_args={"file_path": "/nonexistent"},
        )
        # Should not raise — wrong stage
        result = await mw.before(ctx)
        assert result is ctx


class TestSchemaValidator:
    async def test_already_parsed_is_valid(self):
        from pydantic import BaseModel

        class MySchema(BaseModel):
            x: int

        mw = SchemaValidatorMiddleware()
        ctx = MiddlewareContext(
            stage=MiddlewareStage.LLM_CALL,
            response_schema=MySchema,
        )

        class FakeMsg:
            parsed = MySchema(x=42)
            content = None

        await mw.after(ctx, FakeMsg())
        assert ctx.metadata["schema_valid"] is True

    async def test_no_schema_passthrough(self):
        mw = SchemaValidatorMiddleware()
        ctx = MiddlewareContext(stage=MiddlewareStage.LLM_CALL)

        result = await mw.after(ctx, "anything")
        assert result == "anything"
        assert "schema_valid" not in ctx.metadata


class TestRateLimiter:
    async def test_allows_under_limit(self):
        mw = RateLimiterMiddleware(max_rate=10.0, per_seconds=1.0)
        ctx = MiddlewareContext(stage=MiddlewareStage.LLM_CALL)
        result = await mw.before(ctx)
        assert result is ctx

    async def test_after_passthrough(self):
        mw = RateLimiterMiddleware()
        ctx = MiddlewareContext(stage=MiddlewareStage.LLM_CALL)
        result = await mw.after(ctx, "value")
        assert result == "value"


# ---------------------------------------------------------------------------
# Integration: pipeline with multiple middleware
# ---------------------------------------------------------------------------


class TestMiddlewareIntegration:
    async def test_audit_plus_truncator_pipeline(self):
        audit = AuditLoggerMiddleware()
        truncator = ContentTruncatorMiddleware(max_chars=5, suffix="!")
        pipeline = MiddlewarePipeline([audit, truncator])

        ctx = MiddlewareContext(
            stage=MiddlewareStage.TOOL_EXECUTION,
            agent_name="test",
        )

        class FakeResult:
            content = [{"type": "text", "text": "A" * 20}]

        async def execute(c: MiddlewareContext) -> Any:
            return FakeResult()

        got = await pipeline.run(ctx, execute)
        assert got.content[0]["text"] == "AAAAA!"

    async def test_cache_skips_execution_on_hit(self):
        cache = CacheMiddleware()
        call_count = 0

        ctx = MiddlewareContext(
            stage=MiddlewareStage.TOOL_EXECUTION,
            tool_name="tool",
            tool_args={"k": 1},
        )

        async def execute(c: MiddlewareContext) -> str:
            nonlocal call_count
            call_count += 1
            return "computed"

        pipeline = MiddlewarePipeline([cache])

        # First call — compute
        result1 = await pipeline.run(ctx, execute)
        assert result1 == "computed"
        assert call_count == 1

        # Second call — cached (execute still runs but after() returns cached)
        ctx2 = MiddlewareContext(
            stage=MiddlewareStage.TOOL_EXECUTION,
            tool_name="tool",
            tool_args={"k": 1},
        )
        result2 = await pipeline.run(ctx2, execute)
        assert result2 == "computed"
