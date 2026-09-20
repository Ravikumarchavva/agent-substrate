"""Run-log durability contract: entries carry a schema version, core kinds keep
their persisted spellings, and a log with an unknown kind still loads."""

from __future__ import annotations

import os
import uuid

import pytest

from substrate.kernel.runtime.log_entry import RunLogEntry, RunLogKind

_PG_URL = os.environ.get(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/agentdb"
).replace("+asyncpg", "")


def test_core_kind_values_are_the_persisted_strings() -> None:
    # Renaming any of these strands existing logs — this test is the tripwire.
    assert {k.name: k.value for k in RunLogKind} == {
        "RUN_STARTED": "run.started",
        "RUN_RESUMED": "run.resumed",
        "RUN_SUSPENDED": "run.suspended",
        "RUN_COMPLETED": "run.completed",
        "RUN_FAILED": "run.failed",
        "RUN_CANCELLED": "run.cancelled",
        "EFFECT_RESULT": "effect.result",
        "LLM_CALL": "llm.call",
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


async def test_version_and_unknown_kind_round_trip_through_postgres() -> None:
    asyncpg = pytest.importorskip("asyncpg")
    try:
        pool = await asyncpg.create_pool(_PG_URL, min_size=1, max_size=2)
    except Exception:
        pytest.skip("Postgres not reachable")

    from substrate.infrastructure.runtime.event_log import EventLog

    log = EventLog(pool)
    run_id = f"evlog-{uuid.uuid4().hex}"
    try:
        await log.setup()
        await log.append(
            run_id,
            RunLogEntry(run_id=run_id, seq=0, kind=RunLogKind.RUN_STARTED, v=2),
            expected_seq=-1,
        )
        await log.append(
            run_id,
            RunLogEntry(run_id=run_id, seq=1, kind="future.kind", payload={"x": 1}),
            expected_seq=0,
        )
        got = [e async for e in log.read(run_id)]
        assert [(e.kind, e.v) for e in got] == [("run.started", 2), ("future.kind", 1)]
        assert got[1].payload == {"x": 1}
    finally:
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM event_log WHERE run_id = $1", run_id)
        await pool.close()
