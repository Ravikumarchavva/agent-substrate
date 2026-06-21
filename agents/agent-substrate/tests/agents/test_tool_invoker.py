"""Tests for agents/tools/invoker.py — ToolInvoker enforcement core."""

from __future__ import annotations

import asyncio
from typing import Any


from agent_substrate.agents.tools.invoker import ToolInvoker
from agent_substrate.agents.tools.toolbox import Toolbox
from agent_substrate.kernel.tools.approval import ApprovalDecision, ApprovalRequest
from agent_substrate.kernel.tools.chain import ChainPolicy
from agent_substrate.kernel.core.content import ImageBlock, TextBlock
from agent_substrate.kernel.tools import ToolCallRequest, ToolExecutionResult, ToolRisk


# ---------------------------------------------------------------------------
# Fake implementations
# ---------------------------------------------------------------------------


class EchoTool:
    name = "echo"
    description = "Echoes the input."
    risk = ToolRisk.SAFE
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"msg": {"type": "string"}},
    }

    async def execute(self, *, ctx: Any = None, **kwargs: Any) -> ToolExecutionResult:
        return ToolExecutionResult(content=[TextBlock(text=kwargs.get("msg", ""))])


class HighRiskTool:
    name = "send_email"
    description = "Sends an email."
    risk = ToolRisk.HIGH
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"to": {"type": "string"}},
    }

    async def execute(self, *, ctx: Any = None, **kwargs: Any) -> ToolExecutionResult:
        return ToolExecutionResult(
            content=[TextBlock(text=f"email sent to {kwargs.get('to')}")]
        )


class CriticalTool:
    name = "drop_db"
    description = "Drops the database."
    risk = ToolRisk.CRITICAL
    input_schema: dict[str, Any] = {}

    async def execute(self, *, ctx: Any = None, **kwargs: Any) -> ToolExecutionResult:
        return ToolExecutionResult(content=[TextBlock(text="dropped")])


class SlowTool:
    name = "slow_tool"
    description = "Takes forever."
    risk = ToolRisk.SAFE
    input_schema: dict[str, Any] = {}

    async def execute(self, *, ctx: Any = None, **kwargs: Any) -> ToolExecutionResult:
        await asyncio.sleep(999)
        return ToolExecutionResult(content=[TextBlock(text="done")])


class ImageTool:
    name = "image_tool"
    description = "Returns an image."
    risk = ToolRisk.SAFE
    input_schema: dict[str, Any] = {}

    async def execute(self, *, ctx: Any = None, **kwargs: Any) -> ToolExecutionResult:
        return ToolExecutionResult(
            content=[ImageBlock(data=b"PNG_DATA", media_type="image/png")],
        )


class FakeApprovalAllow:
    async def request(self, req: ApprovalRequest) -> ApprovalDecision:
        return ApprovalDecision.APPROVED


class FakeApprovalDeny:
    async def request(self, req: ApprovalRequest) -> ApprovalDecision:
        return ApprovalDecision.DENIED


class FakeApprovalSlow:
    async def request(self, req: ApprovalRequest) -> ApprovalDecision:
        await asyncio.sleep(999)
        return ApprovalDecision.APPROVED


class FakeArtifactStore:
    """In-memory ArtifactStore for testing."""

    def __init__(self) -> None:
        self._data: dict[str, bytes] = {}
        self._pins: set[str] = set()
        self._counter = 0

    async def store(
        self, data: bytes | str, *, content_type: str = "application/octet-stream"
    ) -> str:
        if isinstance(data, str):
            data = data.encode()
        self._counter += 1
        ref = f"ref_{self._counter}"
        self._data[ref] = data
        return ref

    async def resolve(self, ref: str) -> bytes:
        return self._data[ref]

    async def pin(self, ref: str) -> None:
        self._pins.add(ref)

    async def unpin(self, ref: str) -> None:
        self._pins.discard(ref)


def make_registry(*tools: Any) -> Toolbox:
    tb = Toolbox()
    for t in tools:
        tb.add(t)
    return tb


def make_call(name: str, **kwargs: Any) -> ToolCallRequest:
    return ToolCallRequest(name=name, arguments=kwargs)


# ---------------------------------------------------------------------------
# Basic invocation
# ---------------------------------------------------------------------------


async def test_invoke_safe_tool_inline():
    registry = make_registry(EchoTool())
    invoker = ToolInvoker(registry=registry)
    async with invoker.open_session() as session:
        result = await invoker.invoke(make_call("echo", msg="hello"), session=session)
    assert result.status == "ok"
    assert result.text == "hello"
    assert result.artifact_ref is None


async def test_invoke_unknown_tool_error():
    registry = make_registry()
    invoker = ToolInvoker(registry=registry)
    async with invoker.open_session() as session:
        result = await invoker.invoke(make_call("nonexistent"), session=session)
    assert result.status == "error"
    assert "Unknown tool" in result.text


async def test_invoke_self_recursion_blocked():

    class FakeInterpreter:
        name = "code_interpreter"
        session_id = "default"

        async def execute(self, **kw: Any) -> ToolExecutionResult:
            return ToolExecutionResult(content=[TextBlock(text="")])

    registry = make_registry()
    invoker = ToolInvoker(registry=registry)

    async with invoker.open_session() as session:
        result = await invoker.invoke(make_call("tool_chain"), session=session)
    assert result.status == "error"
    assert "Recursive" in result.text or "Unknown tool" in result.text


# ---------------------------------------------------------------------------
# Hosted / provider-defined tools blocked
# ---------------------------------------------------------------------------


async def test_invoke_hosted_tool_blocked():
    class HostedWebSearch:
        name = "web_search"
        description = "Hosted."
        provider_specs = {"openai": {"type": "web_search_preview"}}

    registry = make_registry(HostedWebSearch())
    invoker = ToolInvoker(registry=registry)
    async with invoker.open_session() as session:
        result = await invoker.invoke(make_call("web_search"), session=session)
    assert result.status == "error"
    assert "provider-hosted" in result.text


async def test_invoke_provider_defined_tool_blocked():
    class ShellTool:
        name = "shell"
        description = "Shell."
        provider_specs = {"openai": {"type": "shell"}}
        call_types = ("shell_call",)

        async def handle_call(self, call: dict, *, ctx: Any = None) -> dict:
            return {}

    registry = make_registry(ShellTool())
    invoker = ToolInvoker(registry=registry)
    async with invoker.open_session() as session:
        result = await invoker.invoke(make_call("shell"), session=session)
    assert result.status == "error"
    assert "provider-defined" in result.text


# ---------------------------------------------------------------------------
# Risk / approval
# ---------------------------------------------------------------------------


async def test_high_risk_tool_no_handler_denied():
    registry = make_registry(HighRiskTool())
    invoker = ToolInvoker(registry=registry, approval_handler=None)
    async with invoker.open_session() as session:
        result = await invoker.invoke(
            make_call("send_email", to="a@b.com"), session=session
        )
    assert result.status == "denied"
    assert "no ApprovalHandler" in result.text


async def test_high_risk_tool_approved():
    registry = make_registry(HighRiskTool())
    invoker = ToolInvoker(
        registry=registry,
        approval_handler=FakeApprovalAllow(),
        policy=ChainPolicy(max_risk_unapproved=ToolRisk.SAFE),
    )
    async with invoker.open_session() as session:
        result = await invoker.invoke(
            make_call("send_email", to="a@b.com"), session=session
        )
    assert result.status == "ok"


async def test_high_risk_tool_denied_by_handler():
    registry = make_registry(HighRiskTool())
    invoker = ToolInvoker(
        registry=registry,
        approval_handler=FakeApprovalDeny(),
        policy=ChainPolicy(max_risk_unapproved=ToolRisk.SAFE),
    )
    async with invoker.open_session() as session:
        result = await invoker.invoke(
            make_call("send_email", to="a@b.com"), session=session
        )
    assert result.status == "denied"


async def test_approval_timeout_returns_denied():
    registry = make_registry(HighRiskTool())
    invoker = ToolInvoker(
        registry=registry,
        approval_handler=FakeApprovalSlow(),
        policy=ChainPolicy(max_risk_unapproved=ToolRisk.SAFE, approval_timeout_s=0.05),
    )
    async with invoker.open_session() as session:
        result = await invoker.invoke(
            make_call("send_email", to="a@b.com"), session=session
        )
    assert result.status == "denied"
    assert "timed out" in result.text.lower()


# ---------------------------------------------------------------------------
# Budget enforcement
# ---------------------------------------------------------------------------


async def test_budget_exhausted():
    registry = make_registry(EchoTool())
    invoker = ToolInvoker(registry=registry, policy=ChainPolicy(max_tool_calls=2))
    async with invoker.open_session() as session:
        await invoker.invoke(make_call("echo", msg="1"), session=session)
        await invoker.invoke(make_call("echo", msg="2"), session=session)
        result = await invoker.invoke(make_call("echo", msg="3"), session=session)
    assert result.status == "error"
    assert "budget" in result.text.lower()


# ---------------------------------------------------------------------------
# Per-call timeout
# ---------------------------------------------------------------------------


async def test_per_call_timeout():
    registry = make_registry(SlowTool())
    invoker = ToolInvoker(registry=registry, policy=ChainPolicy(call_timeout_s=0.05))
    async with invoker.open_session() as session:
        result = await invoker.invoke(make_call("slow_tool"), session=session)
    assert result.status == "error"
    assert "timed out" in result.text.lower()


# ---------------------------------------------------------------------------
# Result shaping: inline vs offload
# ---------------------------------------------------------------------------


async def test_small_result_inline():
    registry = make_registry(EchoTool())
    store = FakeArtifactStore()
    invoker = ToolInvoker(
        registry=registry,
        artifact_store=store,
        policy=ChainPolicy(max_inline_result_bytes=4096),
    )
    async with invoker.open_session() as session:
        result = await invoker.invoke(make_call("echo", msg="short"), session=session)
    assert result.status == "ok"
    assert result.artifact_ref is None
    assert result.text == "short"


async def test_large_result_offloaded():
    big_msg = "x" * 8000

    class BigTool:
        name = "big_tool"
        description = "Returns big data."
        risk = ToolRisk.SAFE
        input_schema: dict[str, Any] = {}

        async def execute(
            self, *, ctx: Any = None, **kwargs: Any
        ) -> ToolExecutionResult:
            return ToolExecutionResult(content=[TextBlock(text=big_msg)])

    registry = make_registry(BigTool())
    store = FakeArtifactStore()
    invoker = ToolInvoker(
        registry=registry,
        artifact_store=store,
        policy=ChainPolicy(max_inline_result_bytes=4096),
    )
    session = invoker.open_session()
    async with session:
        result = await invoker.invoke(make_call("big_tool"), session=session)
        assert result.status == "ok"
        assert result.artifact_ref is not None
        assert len(result.text) < len(big_msg)
        assert result.text.endswith("…")
        # Artifact is pinned while session is alive
        assert result.artifact_ref in session._pinned_refs
    # After close it's cleared
    assert result.artifact_ref not in session._pinned_refs


# ---------------------------------------------------------------------------
# Inbound ref resolution
# ---------------------------------------------------------------------------


async def test_inbound_artifact_ref_resolved():
    """ToolInvoker should resolve {"$artifact": ref} args server-side."""
    received_args: dict[str, Any] = {}

    class RecordingTool:
        name = "recording_tool"
        description = "Records args."
        risk = ToolRisk.SAFE
        input_schema: dict[str, Any] = {"type": "object", "properties": {"data": {}}}

        async def execute(
            self, *, ctx: Any = None, **kwargs: Any
        ) -> ToolExecutionResult:
            received_args.update(kwargs)
            return ToolExecutionResult(content=[TextBlock(text="ok")])

    store = FakeArtifactStore()
    ref = await store.store(b"big_csv_data", content_type="text/csv")

    registry = make_registry(RecordingTool())
    invoker = ToolInvoker(registry=registry, artifact_store=store)
    async with invoker.open_session() as session:
        result = await invoker.invoke(
            make_call("recording_tool", data={"$artifact": ref}), session=session
        )

    assert result.status == "ok"
    # Tool should have received resolved bytes, not the ref dict
    assert received_args.get("data") == b"big_csv_data"


# ---------------------------------------------------------------------------
# Call trace
# ---------------------------------------------------------------------------


async def test_call_trace_populated():
    registry = make_registry(EchoTool())
    invoker = ToolInvoker(registry=registry)
    async with invoker.open_session() as session:
        await invoker.invoke(make_call("echo", msg="a"), session=session)
        await invoker.invoke(make_call("echo", msg="b"), session=session)
        trace = session.trace
    assert len(trace) == 2
    assert all(r.tool == "echo" for r in trace)
    assert all(r.status == "ok" for r in trace)


async def test_call_trace_includes_errors():
    registry = make_registry()
    invoker = ToolInvoker(registry=registry)
    async with invoker.open_session() as session:
        await invoker.invoke(make_call("nonexistent"), session=session)
        trace = session.trace
    assert len(trace) == 1
    assert trace[0].status == "error"


# ---------------------------------------------------------------------------
# Media blocks → ChainFile
# ---------------------------------------------------------------------------


async def test_image_block_produces_chain_file():
    registry = make_registry(ImageTool())
    store = FakeArtifactStore()
    invoker = ToolInvoker(registry=registry, artifact_store=store)
    async with invoker.open_session() as session:
        result = await invoker.invoke(make_call("image_tool"), session=session)
    assert result.status == "ok"
    assert len(result.files) == 1
    assert result.files[0].media_type == "image/png"
    assert result.files[0].artifact_ref is not None
