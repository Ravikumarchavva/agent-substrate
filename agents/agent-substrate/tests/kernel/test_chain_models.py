"""Tests for kernel/chain.py value types."""

from __future__ import annotations

from ravi.kernel.tools.chain import (
    ChainCallRecord,
    ChainFile,
    ChainPolicy,
    ChainRunResult,
    InvocationResult,
)
from ravi.kernel.tools import ToolRisk


def test_chain_policy_defaults():
    p = ChainPolicy()
    assert p.max_tool_calls == 50
    assert p.call_timeout_s == 60.0
    assert p.approval_timeout_s == 55.0
    assert p.total_timeout_s == 300.0
    assert p.max_inline_result_bytes == 4096
    assert p.max_risk_unapproved == ToolRisk.SAFE


def test_chain_policy_json_round_trip():
    p = ChainPolicy(max_tool_calls=10, call_timeout_s=30.0)
    raw = p.model_dump()
    p2 = ChainPolicy.model_validate(raw)
    assert p2 == p


def test_invocation_result_ok():
    r = InvocationResult(status="ok", text="hello", structured={"k": "v"})
    assert r.status == "ok"
    assert r.text == "hello"
    assert r.artifact_ref is None
    assert r.files == []


def test_invocation_result_with_artifact():
    f = ChainFile(
        path="/workspace/img.png", media_type="image/png", artifact_ref="ref_abc"
    )
    r = InvocationResult(
        status="ok",
        text="preview...",
        artifact_ref="ref_abc",
        files=[f],
    )
    raw = r.model_dump()
    r2 = InvocationResult.model_validate(raw)
    assert r2 == r
    assert r2.files[0].artifact_ref == "ref_abc"


def test_invocation_result_denied():
    r = InvocationResult(status="denied", text="Approval denied.")
    assert r.status == "denied"


def test_chain_call_record():
    rec = ChainCallRecord(
        tool="my_tool", args_digest="abc123", status="ok", duration_ms=42
    )
    raw = rec.model_dump()
    rec2 = ChainCallRecord.model_validate(raw)
    assert rec2 == rec


def test_chain_run_result_ok():
    trace = [ChainCallRecord(tool="t", args_digest="d", status="ok", duration_ms=10)]
    r = ChainRunResult(
        status="ok",
        output_text="done",
        tool_calls=1,
        duration_ms=200,
        call_trace=trace,
    )
    raw = r.model_dump()
    r2 = ChainRunResult.model_validate(raw)
    assert r2 == r
    assert len(r2.call_trace) == 1


def test_chain_run_result_error():
    r = ChainRunResult(
        status="error",
        error="Something went wrong.",
        tool_calls=2,
        duration_ms=500,
    )
    assert r.error == "Something went wrong."
    assert r.call_trace == []


def test_chain_run_result_timeout():
    r = ChainRunResult(status="timeout", duration_ms=300_000)
    assert r.status == "timeout"
