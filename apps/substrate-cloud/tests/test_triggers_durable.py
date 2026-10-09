"""Crons, webhooks and conditions are in memory, so a restart used to forget them. They are now kept, and registered again on start — a webhook with the
secret its callers already hold."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from substrate.integrations.triggers.conditions import ConditionMonitor
from substrate.integrations.triggers.webhooks import WebhookRegistry
from substrate_cloud.monolith.services import trigger_store

from test_scheduled_notifications import session
from test_triggers_tenancy import _Scheduler


@pytest.mark.requires_postgres
async def test_triggers_come_back_after_a_restart_and_deleted_ones_do_not():
    async with session() as c:
        app = c._transport.app
        # The running app's own registries stand in for "before the restart".
        scheduler_before = app.state.trigger_scheduler
        webhooks_before, conditions_before = (
            app.state.webhook_registry,
            app.state.condition_monitor,
        )
        app.state.trigger_scheduler = _Scheduler()
        app.state.webhook_registry = WebhookRegistry()
        app.state.condition_monitor = ConditionMonitor()
        try:
            assert (
                await c.post(
                    "/triggers/cron",
                    json={
                        "name": "nightly",
                        "schedule": "0 3 * * *",
                        "target_name": "report",
                    },
                )
            ).status_code == 200
            assert (
                await c.post(
                    "/triggers/cron",
                    json={"name": "gone", "schedule": "0 4 * * *", "target_name": "x"},
                )
            ).status_code == 200
            assert (await c.delete("/triggers/cron/gone")).status_code == 200
            hook = (
                await c.post(
                    "/triggers/webhooks",
                    json={"name": "deploy", "path": "deploy", "target_name": "ship"},
                )
            ).json()
            assert (
                await c.post(
                    "/triggers/conditions",
                    json={
                        "name": "on-fail",
                        "event_type": "job.failed",
                        "target_name": "alert",
                    },
                )
            ).status_code == 200

            # "Restart": empty registries, then restore from what was kept.
            scheduler, webhooks, conditions = (
                _Scheduler(),
                WebhookRegistry(),
                ConditionMonitor(),
            )
            restored = await trigger_store.restore(
                app.state.session_factory,
                scheduler=scheduler,
                webhooks=webhooks,
                conditions=conditions,
            )
            assert restored >= 3
            mine = [
                t for t in scheduler.list_triggers() if t.name == f"{c.tenant}::nightly"
            ]
            assert len(mine) == 1 and mine[0].schedule == "0 3 * * *"
            assert not [
                t for t in scheduler.list_triggers() if t.name.endswith("::gone")
            ]
            [back] = [
                w for w in webhooks.list_webhooks() if w.name == f"{c.tenant}::deploy"
            ]
            assert back.secret == hook["secret"]
            assert [
                x
                for x in conditions.list_conditions()
                if x.name == f"{c.tenant}::on-fail"
            ]
        finally:
            app.state.trigger_scheduler = (
                scheduler_before  # so shutdown stops the real ones
            )
            app.state.webhook_registry, app.state.condition_monitor = (
                webhooks_before,
                conditions_before,
            )
            async with app.state.session_factory() as db:
                await db.execute(
                    text("SELECT set_config('app.bypass_rls', 'on', false)")
                )
                await db.execute(
                    text("DELETE FROM trigger_records WHERE tenant_id = :t"),
                    {"t": c.tenant},
                )
                await db.commit()
