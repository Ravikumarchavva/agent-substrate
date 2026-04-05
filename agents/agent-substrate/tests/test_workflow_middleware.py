from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel

from raavan.core.execution.context import ExecutionContext
from raavan.core.pipelines.middleware import (
    BaseWorkflowMiddleware,
    WorkflowMiddlewareContext,
    WorkflowRunnable,
)
from raavan.integrations.llm.openai.openai_client import _normalize_strict_json_schema


class _RecordingWorkflowMiddleware(BaseWorkflowMiddleware):
    def __init__(self, name: str = "workflow_recorder") -> None:
        super().__init__(name)
        self.before_calls = 0
        self.after_calls = 0
        self.error_calls = 0

    async def before(self, ctx: WorkflowMiddlewareContext) -> WorkflowMiddlewareContext:
        self.before_calls += 1
        ctx.metadata["workflow_seen"] = True
        return ctx

    async def after(self, ctx: WorkflowMiddlewareContext, result: Any) -> Any:
        self.after_calls += 1
        return result

    async def on_error(
        self, ctx: WorkflowMiddlewareContext, error: Exception
    ) -> Optional[Any]:
        self.error_calls += 1
        return None


class _FakeRunnable:
    def __init__(self) -> None:
        self.calls = 0
        self.execution_context: Any = None
        self.seen_parent_context: Any = None

    async def run(self, input_text: str) -> str:
        self.calls += 1
        self.seen_parent_context = self.execution_context.parent_context
        return f"ran:{input_text}"


class _FakeRouter:
    async def route(self, messages: Any) -> tuple[str, str]:
        return ("decision", f"routed:{messages}")


def test_normalize_strict_json_schema_sets_additional_properties_false() -> None:
    class NestedSchema(BaseModel):
        city: str

    class RootSchema(BaseModel):
        vendor: str
        nested: NestedSchema

    raw_schema = RootSchema.model_json_schema()
    normalized = _normalize_strict_json_schema(raw_schema)

    assert normalized["additionalProperties"] is False
    assert normalized["$defs"]["NestedSchema"]["additionalProperties"] is False


async def test_workflow_runnable_wraps_run() -> None:
    middleware = _RecordingWorkflowMiddleware()
    parent_context = ExecutionContext(
        run_id="run-123",
        correlation_id="corr-123",
        thread_id="thread-123",
        metadata={"trace_id": "trace-xyz"},
    )
    fake_runnable = _FakeRunnable()
    runnable = WorkflowRunnable(
        fake_runnable,
        pipeline_name="demo",
        pipeline_id="pipeline-1",
        middleware=[middleware],
        parent_context=parent_context,
    )

    result = await runnable.run("hello")

    assert result == "ran:hello"
    assert middleware.before_calls == 1
    assert middleware.after_calls == 1
    assert middleware.error_calls == 0
    assert fake_runnable.execution_context is None
    assert fake_runnable.seen_parent_context is parent_context


async def test_workflow_runnable_wraps_route() -> None:
    middleware = _RecordingWorkflowMiddleware()
    runnable = WorkflowRunnable(
        _FakeRouter(),
        pipeline_name="router-demo",
        pipeline_id="pipeline-2",
        middleware=[middleware],
    )

    decision, result = await runnable.route(["message"])

    assert decision == "decision"
    assert result == "routed:['message']"
    assert middleware.before_calls == 1
    assert middleware.after_calls == 1


async def test_workflow_context_inherits_parent_metadata() -> None:
    parent = ExecutionContext(
        run_id="run-42",
        correlation_id="corr-42",
        metadata={"trace_id": "trace-42", "shared": "parent"},
    )
    ctx = WorkflowMiddlewareContext(
        pipeline_name="demo",
        pipeline_id="pipeline-42",
        metadata={"shared": "workflow", "local": "yes"},
        parent_context=parent,
    )

    assert ctx.root_context is parent
    assert ctx.inherited_metadata() == {
        "trace_id": "trace-42",
        "shared": "workflow",
        "local": "yes",
    }
