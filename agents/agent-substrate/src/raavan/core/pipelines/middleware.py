"""Workflow-level middleware for multi-node pipeline execution.

This module is intentionally separate from ``raavan.core.middleware``.

- ``raavan.core.middleware`` applies to individual agent internals such as
  one LLM call or one tool execution.
- ``raavan.core.pipelines.middleware`` applies to the workflow as a whole:
  a multi-node pipeline, router, while-loop, or condition graph.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any, AsyncIterator, List, Optional

from raavan.core.execution.context import ExecutionContext
from raavan.core.execution.pipeline import ExecutionMiddlewarePipeline


class WorkflowStage(str, Enum):
    """Workflow execution entrypoints that middleware can intercept."""

    RUN = "workflow_run"
    STREAM = "workflow_stream"
    ROUTE = "workflow_route"


@dataclass
class WorkflowMiddlewareContext(ExecutionContext):
    """Context passed through workflow middleware."""

    pipeline_name: str = ""
    pipeline_id: str = ""
    stage: WorkflowStage = WorkflowStage.RUN


class BaseWorkflowMiddleware(ABC):
    """Base class for workflow-level middleware."""

    def __init__(self, name: str) -> None:
        self.name = name

    @abstractmethod
    async def before(
        self, ctx: WorkflowMiddlewareContext
    ) -> WorkflowMiddlewareContext: ...

    @abstractmethod
    async def after(self, ctx: WorkflowMiddlewareContext, result: Any) -> Any: ...

    async def on_error(
        self, ctx: WorkflowMiddlewareContext, error: Exception
    ) -> Optional[Any]:
        return None


class WorkflowMiddlewarePipeline:
    """Sequential workflow middleware pipeline."""

    def __init__(
        self, middleware: Optional[List[BaseWorkflowMiddleware]] = None
    ) -> None:
        self._pipeline = ExecutionMiddlewarePipeline[
            WorkflowMiddlewareContext, BaseWorkflowMiddleware
        ](middleware)

    @property
    def middleware(self) -> List[BaseWorkflowMiddleware]:
        return self._pipeline.middleware

    def add(self, middleware: BaseWorkflowMiddleware) -> None:
        self._pipeline.add(middleware)

    async def run(self, ctx: WorkflowMiddlewareContext, execute_fn) -> Any:
        return await self._pipeline.run(ctx, execute_fn)


class WorkflowRunnable:
    """Wrap a built pipeline runnable with workflow middleware."""

    def __init__(
        self,
        runnable: Any,
        *,
        pipeline_name: str,
        pipeline_id: str,
        middleware: Optional[List[BaseWorkflowMiddleware]] = None,
        parent_context: Optional[ExecutionContext] = None,
    ) -> None:
        self._runnable = runnable
        self._pipeline = WorkflowMiddlewarePipeline(middleware)
        self._pipeline_name = pipeline_name
        self._pipeline_id = pipeline_id
        self._parent_context = parent_context

    def __getattr__(self, name: str) -> Any:
        return getattr(self._runnable, name)

    def _apply_execution_context(self, ctx: WorkflowMiddlewareContext) -> Any:
        previous = getattr(self._runnable, "execution_context", None)
        if hasattr(self._runnable, "execution_context"):
            self._runnable.execution_context = ctx
        return previous

    def _restore_execution_context(self, previous: Any) -> None:
        if hasattr(self._runnable, "execution_context"):
            self._runnable.execution_context = previous

    async def run(self, input_text: str, *args: Any, **kwargs: Any) -> Any:
        ctx = WorkflowMiddlewareContext(
            pipeline_name=self._pipeline_name,
            pipeline_id=self._pipeline_id,
            stage=WorkflowStage.RUN,
            input_text=input_text,
            run_id=self._parent_context.run_id if self._parent_context else "",
            correlation_id=(
                self._parent_context.correlation_id if self._parent_context else ""
            ),
            thread_id=self._parent_context.thread_id if self._parent_context else "",
            metadata=(
                self._parent_context.inherited_metadata()
                if self._parent_context is not None
                else {}
            ),
            parent_context=self._parent_context,
        )

        async def _execute(_ctx: WorkflowMiddlewareContext) -> Any:
            return await self._runnable.run(input_text, *args, **kwargs)

        previous = self._apply_execution_context(ctx)
        try:
            return await self._pipeline.run(ctx, _execute)
        finally:
            self._restore_execution_context(previous)

    async def route(self, messages: Any, *args: Any, **kwargs: Any) -> Any:
        ctx = WorkflowMiddlewareContext(
            pipeline_name=self._pipeline_name,
            pipeline_id=self._pipeline_id,
            stage=WorkflowStage.ROUTE,
            input_text=str(messages),
            run_id=self._parent_context.run_id if self._parent_context else "",
            correlation_id=(
                self._parent_context.correlation_id if self._parent_context else ""
            ),
            thread_id=self._parent_context.thread_id if self._parent_context else "",
            metadata=(
                self._parent_context.inherited_metadata()
                if self._parent_context is not None
                else {}
            ),
            parent_context=self._parent_context,
        )

        async def _execute(_ctx: WorkflowMiddlewareContext) -> Any:
            return await self._runnable.route(messages, *args, **kwargs)

        previous = self._apply_execution_context(ctx)
        try:
            return await self._pipeline.run(ctx, _execute)
        finally:
            self._restore_execution_context(previous)

    async def run_stream(
        self, input_text: str, *args: Any, **kwargs: Any
    ) -> AsyncIterator[Any]:
        ctx = WorkflowMiddlewareContext(
            pipeline_name=self._pipeline_name,
            pipeline_id=self._pipeline_id,
            stage=WorkflowStage.STREAM,
            input_text=input_text,
            run_id=self._parent_context.run_id if self._parent_context else "",
            correlation_id=(
                self._parent_context.correlation_id if self._parent_context else ""
            ),
            thread_id=self._parent_context.thread_id if self._parent_context else "",
            metadata=(
                self._parent_context.inherited_metadata()
                if self._parent_context is not None
                else {}
            ),
            parent_context=self._parent_context,
        )

        previous = self._apply_execution_context(ctx)
        try:
            for middleware in self._pipeline.middleware:
                ctx = await middleware.before(ctx)

            last_chunk: Any = None
            try:
                async for chunk in self._runnable.run_stream(
                    input_text, *args, **kwargs
                ):
                    last_chunk = chunk
                    yield chunk
            except Exception as exc:
                fallback = None
                for middleware in reversed(self._pipeline.middleware):
                    maybe_result = await middleware.on_error(ctx, exc)
                    if maybe_result is not None and fallback is None:
                        fallback = maybe_result
                if fallback is None:
                    raise
                yield fallback
                return

            for middleware in reversed(self._pipeline.middleware):
                last_chunk = await middleware.after(ctx, last_chunk)
        finally:
            self._restore_execution_context(previous)
