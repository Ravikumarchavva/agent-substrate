"""An agent can be given a model of its own: the one it thinks with in a conversation with you, in groups and when asked to do a task."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import text

from substrate_cloud.monolith.app import app
from substrate_cloud.monolith.services.agents.model import client_for

from test_scheduled_notifications import session


def deps(**keys: str):
    default = SimpleNamespace(provider="openai", model="gpt-5.4-mini")
    return SimpleNamespace(model_client=default, api_keys=keys, model_client_kwargs={}, chat_model="openai/gpt-5.4-mini")


def test_an_agent_with_no_model_of_its_own_uses_the_deployments():
    d = deps(openai="sk-test")
    assert client_for(d, None) is d.model_client
    assert client_for(d, "") is d.model_client
    assert client_for(d, "openai/gpt-5.4-mini") is d.model_client  # the same model: no second client


def test_an_agent_with_a_model_of_its_own_gets_a_client_for_it():
    d = deps(openai="sk-test", anthropic="sk-ant-test")
    client = client_for(d, "anthropic/claude-sonnet-4-20250514")
    assert client is not d.model_client and getattr(client, "model", None) == "claude-sonnet-4-20250514"


async def _wipe(tenant: str) -> None:
    async with app.state.session_factory() as db:
        await db.execute(text("SELECT set_config('app.bypass_rls', 'on', false)"))
        await db.execute(text("DELETE FROM agents WHERE tenant_id = :t"), {"t": tenant})
        await db.commit()


@pytest.mark.requires_postgres
async def test_a_model_is_chosen_checked_against_the_credentials_and_reports_whether_it_sees():
    async with session() as c:
        ctx = app.state.ctx
        keys = dict(ctx.api_keys)
        ctx.api_keys = {"openai": "sk-test"}
        try:
            made = (await c.post("/agents", json={"name": "Scout", "model": "openai/gpt-5.4-mini"})).json()
            assert made["model"] == "openai/gpt-5.4-mini" and made["sees"] is True

            nope = await c.post("/agents", json={"name": "Quill", "model": "anthropic/claude-sonnet-4-20250514"})
            assert nope.status_code == 422 and "credentials" in nope.text  # a model the deployment cannot call is refused, not saved

            blind = await c.patch(f"/agents/{made['id']}", json={"model": "openai/gpt-3.5-turbo"})
            assert blind.status_code == 200 and blind.json()["sees"] is False

            cleared = (await c.patch(f"/agents/{made['id']}", json={"default_model": True})).json()
            assert cleared["model"] is None and cleared["sees"] is None  # the deployment's default: not known here
        finally:
            ctx.api_keys = keys
            await _wipe(c.tenant)
