"""Run-log durability contract: entries carry a schema version, core kinds keep
their persisted spellings, and a log with an unknown kind still loads."""

from __future__ import annotations

from substrate.kernel.abstractions.runtime.log_entry import RunLogEntry, RunLogKind


def test_core_kind_values_are_the_persisted_strings() -> None:
    # Renaming any of these strands existing logs — this test is the tripwire.
    assert {k.name: k.value for k in RunLogKind} == {
        "RUN_STARTED": "run.started",
        "RUN_RESUMED": "run.resumed",
        "RUN_SUSPENDED": "run.suspended",
        "RUN_COMPLETED": "run.completed",
        "RUN_FAILED": "run.failed",
        "RUN_CANCELLED": "run.cancelled",
        "RUN_TRUNCATED": "run.truncated",
        "RUN_RETRYING": "run.retrying",
        "EFFECT_INTENT": "effect.intent",
        "EFFECT_RESULT": "effect.result",
        "LLM_CALL": "llm.call",
        "ASSISTANT_MESSAGE": "assistant.message",
        "TOOL_CALL": "tool.call",
        "TOOL_RESULT": "tool.result",
        "USER_MESSAGE": "user.message",
        "USER_MESSAGE_FLAGGED": "user.message.flagged",
        "MCP_APP_CONTEXT": "mcp_app_context",
        "INPUT_REQUESTED": "input.requested",
        "APPROVAL_REQUESTED": "approval.requested",
        "CHILD_SPAWNED": "child.spawned",
        "SUBAGENT_START": "subagent.start",
        "SUBAGENT_DONE": "subagent.done",
        "TEXT_DELTA": "text.delta",
        "REASONING_DELTA": "reasoning.delta",
    }


def test_entry_defaults_to_version_1_and_accepts_unknown_kinds() -> None:
    entry = RunLogEntry(run_id="r", seq=0, kind="some.app.kind")
    assert entry.v == 1
    assert entry.kind == "some.app.kind"
    assert RunLogEntry(run_id="r", seq=0, kind=RunLogKind.RUN_STARTED).kind == "run.started"
