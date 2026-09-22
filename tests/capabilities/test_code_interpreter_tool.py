"""CodeInterpreterTool — resolves a WorkspaceScope from the ambient ContextVars
and threads it through to the runtime, and relays a committed workspace
snapshot id back to ctx so persist_turns can stamp the turn's MessageNode."""

from __future__ import annotations

import pytest

from substrate.agents.storage.tasks import current_agent_id, current_thread_id
from substrate.agents.workspace.scope import current_branch_id, current_tenant_id, current_user_id
from substrate.capabilities.tools.code_interpreter.code_interpreter.runtimes.base import (
    ExecResult,
    NetworkPolicy,
)
from substrate.capabilities.tools.code_interpreter.code_interpreter.tool import (
    CodeInterpreterTool,
)


class _FakeRuntime:
    name = "fake"

    def __init__(self, result: ExecResult) -> None:
        self.result = result
        self.last_spec = None

    async def execute(self, spec):
        self.last_spec = spec
        return self.result

    async def stop(self) -> None:
        pass


class _FakeCtx:
    def __init__(self) -> None:
        self.recorded: list[str] = []

    def record_workspace_snapshot(self, snapshot_id: str) -> None:
        self.recorded.append(snapshot_id)


@pytest.fixture(autouse=True)
def _scope_context():
    t1 = current_tenant_id.set("tenant-a")
    t2 = current_user_id.set("user-a")
    t3 = current_thread_id.set("conv-1")
    t4 = current_branch_id.set("exp-1")
    t5 = current_agent_id.set("primary")
    yield
    current_tenant_id.reset(t1)
    current_user_id.reset(t2)
    current_thread_id.reset(t3)
    current_branch_id.reset(t4)
    current_agent_id.reset(t5)


async def test_execute_scopes_the_spec_to_the_active_branch():
    runtime = _FakeRuntime(ExecResult(stdout="ok"))
    tool = CodeInterpreterTool(runtime, network=NetworkPolicy.DENY)

    await tool.execute(code="print(1)")

    spec = runtime.last_spec
    assert spec is not None
    assert spec.session_dir == "conv-1/exp-1"
    scope = spec.extra["workspace_scope"]
    assert scope.tenant_id == "tenant-a"
    assert scope.user_id == "user-a"
    assert scope.conversation_id == "conv-1"
    assert scope.branch_id == "exp-1"


async def test_private_dir_is_not_nested_under_the_shared_session_dir():
    runtime = _FakeRuntime(ExecResult(stdout="ok"))
    tool = CodeInterpreterTool(runtime, network=NetworkPolicy.DENY)

    await tool.execute(code="print(1)")

    spec = runtime.last_spec
    private_dir = spec.extra["private_dir"]
    assert not private_dir.startswith(spec.session_dir + "/")


async def test_workspace_snapshot_id_is_relayed_to_ctx():
    runtime = _FakeRuntime(ExecResult(stdout="ok", workspace_snapshot_id="snap-123"))
    tool = CodeInterpreterTool(runtime, network=NetworkPolicy.DENY)
    ctx = _FakeCtx()

    await tool.execute(ctx=ctx, code="print(1)")

    assert ctx.recorded == ["snap-123"]


async def test_no_snapshot_id_means_nothing_recorded():
    runtime = _FakeRuntime(ExecResult(stdout="ok", workspace_snapshot_id=None))
    tool = CodeInterpreterTool(runtime, network=NetworkPolicy.DENY)
    ctx = _FakeCtx()

    await tool.execute(ctx=ctx, code="print(1)")

    assert ctx.recorded == []


async def test_missing_scope_returns_error_without_crashing():
    t1 = current_tenant_id.set(None)
    try:
        runtime = _FakeRuntime(ExecResult(stdout="ok"))
        tool = CodeInterpreterTool(runtime, network=NetworkPolicy.DENY)

        result = await tool.execute(code="print(1)")

        assert result.is_error
    finally:
        current_tenant_id.reset(t1)
