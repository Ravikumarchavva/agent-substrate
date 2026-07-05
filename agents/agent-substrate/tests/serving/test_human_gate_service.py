"""human_gate service-layer tests — the signal_bus convergence added in
Phase 2 of the remediation program (resolve_request() now fires a durable
SignalBus signal alongside the legacy Redis pub/sub publish).

Uses the real Postgres test DB (skips if unreachable) — human_gate is
SQLAlchemy-ORM-backed (its own hitl_requests table), separate from the
asyncpg-based ravi_* tables PostgresSignalBus itself uses, but both point at
the same physical database (see human_gate/app.py's lifespan docstring).
"""

from __future__ import annotations

import os
import uuid

import pytest

from substrate.serving.services.base import init_service_db
from substrate.serving.services.human_gate.models import ServiceBase
from substrate.serving.services.human_gate.service import (
    cancel_pending_for_thread,
    create_request,
    get_request,
    resolve_request,
)

pytestmark = [pytest.mark.requires_postgres]

_PG_URL = os.environ.get(
    "DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/agentdb"
)


async def _pg_reachable() -> bool:
    try:
        import asyncpg

        pool = await asyncpg.create_pool(
            _PG_URL.replace("+asyncpg", ""), min_size=1, max_size=1
        )
        await pool.close()
        return True
    except Exception:
        return False


@pytest.fixture
async def db_session():
    if not await _pg_reachable():
        pytest.skip("Postgres not reachable")
    engine, session_factory = await init_service_db(_PG_URL, ServiceBase)
    async with session_factory() as session:
        yield session
        await session.rollback()
    await engine.dispose()


@pytest.fixture
async def signal_bus():
    import asyncpg

    from substrate.infrastructure.runtime.pg_signal_bus import PostgresSignalBus

    pool = await asyncpg.create_pool(_PG_URL.replace("+asyncpg", ""))
    bus = PostgresSignalBus(pool)
    await bus.setup()
    yield bus
    await pool.close()


async def test_resolve_request_fires_durable_signal(db_session, signal_bus) -> None:
    """resolve_request(signal_bus=...) fires hitl:{request_id} on req.run_id
    — the same signal name/shape AskHumanTool's signal-suspend path
    (ctx.sleep_until_signal) waits on."""
    request_id = f"req-{uuid.uuid4().hex}"
    thread_id = uuid.uuid4()
    run_id = f"run-{uuid.uuid4().hex}"

    await create_request(
        db_session,
        request_id=request_id,
        thread_id=thread_id,
        run_id=run_id,
        type="human_input",
        prompt="Pick one",
    )
    await db_session.commit()

    resolved = await resolve_request(
        db_session,
        request_id,
        status="answered",
        response_value="option-a",
        responded_by="tester",
        signal_bus=signal_bus,
    )
    await db_session.commit()

    assert resolved is not None
    assert resolved.status == "answered"

    payload = await signal_bus.consume(run_id, f"hitl:{request_id}", "test-effect-id")
    assert payload is not None
    assert payload["action"] == "answered"
    assert payload["value"] == "option-a"
    assert payload["responded_by"] == "tester"


async def test_resolve_request_without_run_id_does_not_signal(
    db_session, signal_bus
) -> None:
    """A Future-based (non-signal) request has no run_id — resolve_request()
    must not attempt to signal anything for it."""
    request_id = f"req-{uuid.uuid4().hex}"
    thread_id = uuid.uuid4()

    await create_request(
        db_session,
        request_id=request_id,
        thread_id=thread_id,
        run_id=None,
        type="tool_approval",
        tool_name="some_tool",
    )
    await db_session.commit()

    # Must not raise even though there's nothing to signal.
    resolved = await resolve_request(
        db_session,
        request_id,
        status="approved",
        response_value="approved",
        signal_bus=signal_bus,
    )
    await db_session.commit()
    assert resolved is not None
    assert resolved.status == "approved"


async def test_cancel_pending_for_thread_signals_each_request(
    db_session, signal_bus
) -> None:
    thread_id = uuid.uuid4()
    run_id_a = f"run-{uuid.uuid4().hex}"
    run_id_b = f"run-{uuid.uuid4().hex}"
    req_a = f"req-{uuid.uuid4().hex}"
    req_b = f"req-{uuid.uuid4().hex}"

    await create_request(
        db_session,
        request_id=req_a,
        thread_id=thread_id,
        run_id=run_id_a,
        type="human_input",
    )
    await create_request(
        db_session,
        request_id=req_b,
        thread_id=thread_id,
        run_id=run_id_b,
        type="human_input",
    )
    await db_session.commit()

    count = await cancel_pending_for_thread(
        db_session, thread_id, signal_bus=signal_bus
    )
    await db_session.commit()
    assert count == 2

    for run_id, req_id in ((run_id_a, req_a), (run_id_b, req_b)):
        payload = await signal_bus.consume(run_id, f"hitl:{req_id}", f"test-{req_id}")
        assert payload is not None
        assert payload["action"] == "cancelled"

    req_a_row = await get_request(db_session, req_a)
    assert req_a_row.status == "cancelled"
