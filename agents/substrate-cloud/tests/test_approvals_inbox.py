"""``GET /approvals``: what the assistant is waiting on, across the caller's conversations. Real Postgres for the threads, a stub runtime
for the suspended runs (a suspended run cannot be built by hand without the whole engine)."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from substrate.types import RunStatus
from substrate_cloud.monolith.app import app
from substrate_cloud.monolith.security.deps import get_current_user
from substrate_cloud.shared.auth.claims import AuthClaims

pytestmark = pytest.mark.requires_postgres


def entry(kind: str, payload: dict, minute: int = 0):
    return SimpleNamespace(
        kind=kind,
        payload=payload,
        ts=datetime(2026, 10, 4, 9, minute, tzinfo=timezone.utc),
    )


class StubRuntime:
    def __init__(self, runs, log):
        self.store = SimpleNamespace(find_runs=self._find)
        self._runs, self._log = runs, log

    async def _find(self, **kw):
        return [r for r in self._runs if kw.get("tenant") in (None, r.tenant)]

    async def read(self, run_id):
        return self._log.get(run_id, [])


@asynccontextmanager
async def session():
    async with app.router.lifespan_context(app):
        tenant, user = f"apptest-{uuid.uuid4().hex[:10]}", "u1"
        app.dependency_overrides[get_current_user] = lambda: AuthClaims(
            sub=user, tenant_id=tenant
        )
        real = app.state.ctx.runtime
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as c:
                c.tenant = tenant
                yield c
        finally:
            app.state.ctx.runtime = real
            app.dependency_overrides.pop(get_current_user, None)
            async with app.state.session_factory() as db:
                await db.execute(
                    text("SELECT set_config('app.bypass_rls', 'on', false)")
                )
                await db.execute(
                    text("DELETE FROM threads WHERE tenant_id = :t"), {"t": tenant}
                )
                await db.commit()


async def thread(c: AsyncClient, name: str) -> str:
    return (await c.post("/threads", json={"name": name})).json()["id"]


def run(run_id: str, thread_id: str, tenant: str, status=RunStatus.SUSPENDED):
    return SimpleNamespace(
        run_id=run_id, thread_id=thread_id, tenant=tenant, status=status
    )


async def test_pending_approvals_and_questions_are_listed_oldest_first_with_the_conversation():
    async with session() as c:
        a, b = await thread(c, "Weekly report"), await thread(c, "Trip plan")
        app.state.ctx.runtime = StubRuntime(
            [run("r1", a, c.tenant), run("r2", b, c.tenant)],
            {
                "r1": [
                    entry(
                        "approval.requested",
                        {"tool_name": "send_email", "args": {"to": "ann@example.com"}},
                        minute=30,
                    )
                ],
                "r2": [
                    entry("input.requested", {"question": "Which hotel?"}, minute=5)
                ],
            },
        )
        got = (await c.get("/approvals")).json()
        assert [(g["thread_name"], g["kind"]) for g in got] == [
            ("Trip plan", "input"),
            ("Weekly report", "approval"),
        ]
        assert got[0]["summary"] == "Which hotel?"
        assert (
            "send_email" in got[1]["summary"] and "ann@example.com" in got[1]["summary"]
        )


async def test_only_suspended_runs_in_the_callers_own_conversations_are_listed():
    async with session() as c:
        mine = await thread(c, "Mine")
        other_user_thread = str(uuid.uuid4())
        app.state.ctx.runtime = StubRuntime(
            [
                run("r1", mine, c.tenant, status=RunStatus.RUNNING),  # not waiting
                run("r2", other_user_thread, c.tenant),  # not their thread
                run("r3", mine, "another-tenant"),  # not their tenant
            ],
            {
                k: [entry("input.requested", {"question": "?"})]
                for k in ("r1", "r2", "r3")
            },
        )
        assert (await c.get("/approvals")).json() == []


async def test_a_suspended_run_with_nothing_requested_is_not_an_approval():
    async with session() as c:
        t = await thread(c, "Sleeping")
        app.state.ctx.runtime = StubRuntime([run("r1", t, c.tenant)], {"r1": []})
        assert (await c.get("/approvals")).json() == []
