"""A restart must not silently skip a schedule: firings that came due while the server was down run once on start. And "ask before acting" is a
per-task setting, on unless the person turns it off."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text, update

from substrate_cloud.monolith.models import ScheduledTask
from substrate_cloud.monolith.services.scheduled_service import (
    catch_up_missed_tasks,
    missed_firing,
)

from test_scheduled_notifications import session

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)


def test_an_interval_is_missed_when_a_full_period_has_passed():
    assert missed_firing("interval", "600", NOW - timedelta(minutes=11), NOW)
    assert not missed_firing("interval", "600", NOW - timedelta(minutes=5), NOW)
    assert not missed_firing("interval", "soon", NOW - timedelta(days=9), NOW)


def test_a_cron_is_missed_when_it_came_due_since_the_last_run():
    daily = "0 6 * * *"
    assert missed_firing("cron", daily, NOW - timedelta(days=1), NOW)
    assert not missed_firing("cron", daily, NOW - timedelta(minutes=1), NOW)
    assert not missed_firing("cron", "not a cron", NOW - timedelta(days=9), NOW)


@pytest.mark.requires_postgres
async def test_overdue_active_tasks_run_once_and_the_rest_do_not():
    async with session() as c:
        thread = uuid.UUID((await c.post("/threads", json={"name": "t"})).json()["id"])
        ids = {}
        async with c._transport.app.state.session_factory() as db:
            await db.execute(text("SELECT set_config('app.bypass_rls', 'on', false)"))
            for name, status, interval in (
                ("overdue", "active", "60"),
                ("fresh", "active", "86400"),
                ("paused", "paused", "60"),
            ):
                task = ScheduledTask(
                    name=name, prompt="p", cron_expression=interval, kind="interval",
                    thread_id=thread, status=status,
                )  # fmt: skip
                db.add(task)
                await db.flush()
                ids[name] = task.id
            await db.execute(
                update(ScheduledTask)
                .where(ScheduledTask.id.in_(ids.values()))
                .values(created_at=datetime.now(timezone.utc) - timedelta(hours=2))
            )
            await db.commit()

        ran: list[uuid.UUID] = []

        async def run(task_id):
            ran.append(task_id)

        overdue = await catch_up_missed_tasks(
            c._transport.app.state.session_factory, run
        )
        mine = {i for i in overdue if i in ids.values()}
        assert mine == {ids["overdue"]}
        assert set(ran) >= {ids["overdue"]} and ids["fresh"] not in ran
        assert ids["paused"] not in ran


@pytest.mark.requires_postgres
async def test_ask_before_acting_is_on_by_default_and_can_be_turned_off():
    async with session() as c:
        made = (
            await c.post(
                "/scheduled",
                json={"name": "n", "prompt": "p", "cron_expression": "0 6 * * *"},
            )
        ).json()
        assert made["ask_before_acting"] is True
        task_id = made["id"]
        off = (await c.patch(f"/scheduled/{task_id}", json={"ask_before_acting": False})).json()
        assert off["ask_before_acting"] is False
        assert (await c.get(f"/scheduled/{task_id}")).json()["ask_before_acting"] is False
        await c.delete(f"/scheduled/{task_id}")
