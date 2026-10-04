"""What happens around a scheduled run: replicas do not run the same tick twice, a run's cost is read from its journal, and the owner is told
(in the app, and by email if they asked). Postgres for the rows; no model."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from substrate.types import RunLogKind
from substrate_cloud.monolith.app import app
from substrate_cloud.monolith.models import ScheduledTask
from substrate_cloud.monolith.security.deps import get_current_user
from substrate_cloud.monolith.services.notification_service import (
    notify,
    send_email,
)
from substrate_cloud.monolith.services.scheduled_service import (
    claim_firing,
    firing_gap_seconds,
    run_cost,
    waiting_summary,
)
from substrate_cloud.shared.auth.claims import AuthClaims


def entry(kind, payload):
    return SimpleNamespace(kind=kind, payload=payload)


def test_a_runs_cost_is_the_sum_of_its_model_calls():
    log = [
        entry(RunLogKind.LLM_CALL, {"tokens": 1000, "cost_usd": 0.002}),
        entry(RunLogKind.TEXT_DELTA, {"text": "x"}),
        entry(RunLogKind.LLM_CALL, {"tokens": 500, "cost_usd": 0.001}),
        entry(RunLogKind.LLM_CALL, {}),
    ]
    tokens, cost = run_cost(log)
    assert tokens == 1500 and cost == pytest.approx(0.003)
    assert run_cost([]) == (0, 0.0)


def test_what_a_parked_run_is_waiting_for_reads_as_a_sentence():
    assert "send_email" in waiting_summary(
        RunLogKind.APPROVAL_REQUESTED, {"tool_name": "send_email"}
    )
    assert (
        waiting_summary(RunLogKind.INPUT_REQUESTED, {"question": "Which hotel?"})
        == "Which hotel?"
    )


def test_the_gap_that_separates_two_firings_follows_the_schedule():
    assert (
        firing_gap_seconds(SimpleNamespace(kind="cron", cron_expression="* * * * *"))
        == 30
    )
    assert (
        firing_gap_seconds(SimpleNamespace(kind="interval", cron_expression="600"))
        == 300
    )
    assert (
        firing_gap_seconds(SimpleNamespace(kind="interval", cron_expression="x")) == 30
    )


async def test_email_goes_through_resend_and_never_raises():
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={"id": "e1"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    ok = await send_email(
        to="ann@example.com", subject="Report ready", text="hi",
        api_key="re_key", sender="A <a@x.test>", client=client,
    )  # fmt: skip
    assert ok and sent[0].headers["authorization"] == "Bearer re_key"
    assert b"ann@example.com" in sent[0].content

    assert not await send_email(
        to="a@b.c", subject="s", text="t", api_key="", sender="x"
    )
    refused = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(401))
    )
    assert not await send_email(
        to="a@b.c", subject="s", text="t", api_key="k", sender="x", client=refused
    )  # fmt: skip


@asynccontextmanager
async def session():
    async with app.router.lifespan_context(app):
        tenant = f"scheduletest-{uuid.uuid4().hex[:10]}"
        app.dependency_overrides[get_current_user] = lambda: AuthClaims(
            sub="u1", tenant_id=tenant
        )
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as c:
                c.tenant = tenant
                yield c
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            async with app.state.session_factory() as db:
                await db.execute(
                    text("SELECT set_config('app.bypass_rls', 'on', false)")
                )
                await db.execute(
                    text("DELETE FROM notifications WHERE tenant_id = :t"),
                    {"t": tenant},
                )
                await db.execute(
                    text("DELETE FROM threads WHERE tenant_id = :t"), {"t": tenant}
                )
                await db.commit()


@pytest.mark.requires_postgres
async def test_only_one_replica_wins_a_tick():
    async with session() as c:
        thread = (await c.post("/threads", json={"name": "t"})).json()["id"]
        async with app.state.session_factory() as db:
            await db.execute(text("SELECT set_config('app.bypass_rls', 'on', false)"))
            task = ScheduledTask(
                name="n",
                prompt="p",
                cron_expression="* * * * *",
                thread_id=uuid.UUID(thread),
            )
            db.add(task)
            await db.commit()
            task_id = task.id
        async with app.state.session_factory() as a, app.state.session_factory() as b:
            for db in (a, b):
                await db.execute(
                    text("SELECT set_config('app.bypass_rls', 'on', false)")
                )
            assert await claim_firing(a, task_id, min_gap_s=30) is True
            assert (
                await claim_firing(b, task_id, min_gap_s=30) is False
            )  # the other replica heard the same tick
            assert (
                await claim_firing(b, task_id, min_gap_s=0) is True
            )  # a later tick is a new claim


@pytest.mark.requires_postgres
async def test_notifications_are_listed_counted_marked_read_and_private():
    async with session() as c:
        async with app.state.session_factory() as db:
            for kind, title in (
                ("task_run", "Report ready"),
                ("approval", "Needs you"),
            ):
                await notify(
                    db,
                    tenant_id=c.tenant,
                    user_identifier="u1",
                    kind=kind,
                    title=title,
                    body="b",
                )
            await notify(
                db,
                tenant_id=c.tenant,
                user_identifier="someone-else",
                kind="task_run",
                title="not mine",
            )
            await db.commit()
        got = (await c.get("/notifications")).json()
        assert got["unread"] == 2 and [n["title"] for n in got["items"]] == [
            "Needs you",
            "Report ready",
        ]

        first = got["items"][0]["id"]
        assert (await c.post(f"/notifications/{first}/read")).status_code == 204
        assert (await c.get("/notifications")).json()["unread"] == 1
        assert (await c.post("/notifications/read-all")).status_code == 204
        assert (await c.get("/notifications")).json()["unread"] == 0
        assert (await c.post(f"/notifications/{uuid.uuid4()}/read")).status_code == 404
