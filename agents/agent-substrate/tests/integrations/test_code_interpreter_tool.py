"""CodeInterpreterTool — resolves a WorkspaceScope from the ambient ContextVars
and threads it through to the runtime, and relays a committed workspace
snapshot id back to ctx so persist_turns can stamp the turn's MessageNode."""

from __future__ import annotations

from substrate.types import RunScope

from substrate.integrations.tools.code_interpreter.code_interpreter.runtimes.base import (
    ExecResult,
    NetworkPolicy,
)
from substrate.integrations.tools.code_interpreter.code_interpreter.tool import (
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


SCOPE = RunScope(
    tenant_id="tenant-a",
    user_id="user-a",
    thread_id="conv-1",
    branch_id="exp-1",
    agent_id="primary",
)


class _FakeCtx:
    def __init__(self, scope: RunScope = SCOPE) -> None:
        self.scope = scope
        self.recorded: list[str] = []

    def record_workspace_snapshot(self, snapshot_id: str) -> None:
        self.recorded.append(snapshot_id)


async def test_execute_scopes_the_spec_to_the_active_branch():
    runtime = _FakeRuntime(ExecResult(stdout="ok"))
    tool = CodeInterpreterTool(runtime, network=NetworkPolicy.DENY)

    await tool.execute(ctx=_FakeCtx(), code="print(1)")

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

    await tool.execute(ctx=_FakeCtx(), code="print(1)")

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
    runtime = _FakeRuntime(ExecResult(stdout="ok"))
    tool = CodeInterpreterTool(runtime, network=NetworkPolicy.DENY)

    result = await tool.execute(
        ctx=_FakeCtx(RunScope(tenant_id=None, user_id="user-a")), code="print(1)"
    )

    assert result.is_error


async def test_no_ctx_at_all_returns_error_without_crashing():
    tool = CodeInterpreterTool(
        _FakeRuntime(ExecResult(stdout="ok")), network=NetworkPolicy.DENY
    )
    assert (await tool.execute(code="print(1)")).is_error


HOME = "dot-6f1c0a2e-0000-4000-8000-000000000001"
GROUP = "group-6f1c0a2e-0000-4000-8000-000000000002"
GROUP_2 = "group-6f1c0a2e-0000-4000-8000-000000000004"


def _agent_scope(mounts=(("trip", GROUP),), workspace_id: str = HOME) -> RunScope:
    return RunScope(
        tenant_id="tenant-a",
        user_id="user-a",
        thread_id="conv-1",
        branch_id="exp-1",
        workspace_id=workspace_id,
        mounts=tuple(mounts),
        agent_id="primary",
    )


async def test_an_agents_home_and_its_groups_are_the_workspaces_the_code_gets():
    runtime = _FakeRuntime(ExecResult(stdout="ok"))
    tool = CodeInterpreterTool(runtime, network=NetworkPolicy.DENY)

    await tool.execute(ctx=_FakeCtx(_agent_scope((("trip", GROUP), ("kitchen", GROUP_2)))), code="print(1)")

    spec = runtime.last_spec
    assert spec.session_dir == f"{HOME}/main"  # a home is never branched, whatever branch the conversation asking is on
    assert spec.extra["workspace_scope"].conversation_id == HOME
    assert [(m.label, m.session_dir, m.scope.conversation_id, m.scope.branch_id) for m in spec.mounts] == [
        ("trip", f"{GROUP}/main", GROUP, "main"),
        ("kitchen", f"{GROUP_2}/main", GROUP_2, "main"),
    ]
    assert all(m.scope.user_id == "user-a" and m.scope.tenant_id == "tenant-a" for m in spec.mounts)


async def test_a_conversations_own_workspace_still_follows_its_branch_and_has_no_mounts():
    runtime = _FakeRuntime(ExecResult(stdout="ok"))
    await CodeInterpreterTool(runtime).execute(ctx=_FakeCtx(), code="print(1)")
    assert runtime.last_spec.session_dir == "conv-1/exp-1" and runtime.last_spec.mounts == ()


async def test_a_mount_that_is_not_a_home_or_a_drive_is_refused_before_anything_runs():
    bad = [
        (("trip", "conv-2"),),  # another conversation's workspace
        (("trip", "group-not-a-uuid"),),
        (("../etc", GROUP),),  # a label is a folder name, nothing more
        (("a/b", GROUP),),
        (("private", GROUP),),  # /workspace/private is the agent's own
        (("", GROUP),),
        tuple((f"g{i}", f"group-6f1c0a2e-0000-4000-8000-{i:012d}") for i in range(40)),  # too many to mount
    ]
    for mounts in bad:
        runtime = _FakeRuntime(ExecResult(stdout="ok"))
        result = await CodeInterpreterTool(runtime).execute(ctx=_FakeCtx(_agent_scope(mounts)), code="print(1)")
        assert result.is_error, mounts
        assert runtime.last_spec is None, mounts
