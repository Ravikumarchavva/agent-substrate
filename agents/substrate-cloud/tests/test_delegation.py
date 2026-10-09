"""``ask_agent``: an agent asks another of the user's agents and gets its answer, in a conversation of its own, one level deep and capped."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from substrate.testing.scripted import ScriptedModel
from substrate.types import RunScope
from substrate_cloud.monolith.services.agents.delegation import (
    MAX_CALLS_PER_MESSAGE,
    AgentRef,
    AskAgentTool,
    other_agents,
)
from substrate_cloud.monolith.services.agents.pairs import _line

from test_scheduled_notifications import session


def agent(name="Scout", role="Researcher"):
    return AgentRef(id=uuid.uuid4(), name=name, role=role)


def run_ctx(tenant="t", user="u", thread="t1"):
    return SimpleNamespace(
        scope=RunScope(tenant_id=tenant, user_id=user, thread_id=thread)
    )


async def test_it_refuses_unknown_agents_empty_requests_and_anonymous_runs():
    tool = AskAgentTool(SimpleNamespace(), [agent("Scout")], lambda t: t.name)
    assert "Scout" in tool.description and "Researcher" in tool.description
    unknown = await tool.execute(ctx=run_ctx(), agent="Nobody", request="hi")
    assert unknown.is_error and "scout" in unknown.text.lower()
    assert (await tool.execute(ctx=run_ctx(), agent="Scout", request="  ")).is_error
    assert (
        await tool.execute(
            ctx=SimpleNamespace(scope=RunScope()), agent="Scout", request="hi"
        )
    ).is_error


async def test_it_stops_after_a_few_asks_per_message():
    tool = AskAgentTool(SimpleNamespace(), [agent("Scout")], lambda t: t.name)
    tool._calls = MAX_CALLS_PER_MESSAGE  # noqa: SLF001
    result = await tool.execute(ctx=run_ctx(), agent="Scout", request="again")
    assert result.is_error and str(MAX_CALLS_PER_MESSAGE) in result.text


@pytest.mark.requires_postgres
async def test_the_other_agent_answers_in_its_own_archived_conversation():
    async with session() as c:
        app = c._transport.app
        ctx = app.state.ctx
        made = (
            await c.post("/agents", json={"name": "Scout", "role": "Researcher"})
        ).json()
        parent = (await c.post("/threads", json={"name": "parent"})).json()["id"]
        real_model = ctx.model_client
        ctx.model_client = ScriptedModel("The answer is 42.")
        try:
            # Read the way the chat route reads them: after the database session has closed, so nothing may depend on it.
            others = await other_agents(ctx, c.tenant, "u1", exclude=None)
            assert [a.name for a in others] == ["Scout"]
            tool = AskAgentTool(ctx, others, lambda t: getattr(t, "name", ""))
            result = await tool.execute(
                ctx=run_ctx(tenant=c.tenant, user="u1", thread=parent),
                agent="scout",
                request="What is 6 x 7?",
            )
        finally:
            ctx.model_client = real_model
        assert not result.is_error, result.text
        delegate = result.structured_content["thread_id"]
        # What the asker got back is what the other agent said in its own conversation. (Compared to the journal rather than to the scripted
        # text: a server running against the same database may pick the run up first and answer with its real model.)
        said = (
            await c.get(f"/threads/{delegate}/export", params={"format": "json"})
        ).json()["messages"]
        assert (
            result.text
            == [m for m in said if m["role"] == "assistant"][-1]["text"].strip()
        )

        # The delegate's conversation exists, belongs to the agent, and is tucked away in Archived, linked to the asker.
        shown = (await c.get(f"/threads/{delegate}")).json()
        assert shown["agent_id"] == made["id"] and shown["archived_at"] is not None
        assert shown["metadata"] == {
            "delegated_from": parent,
            "request": "What is 6 x 7?",
        }  # a plain chat asked: no agent is the asker
        assert (await c.get(f"/threads/{delegate}/runs")).json()[0][
            "status"
        ] == "completed"


@pytest.mark.requires_postgres
async def test_what_one_agent_asks_another_is_kept_as_their_conversation_and_a_plain_chat_is_not_one():
    async with session() as c:
        app = c._transport.app
        ctx = app.state.ctx
        relay = (await c.post("/agents", json={"name": "Relay"})).json()
        atlas = (
            await c.post("/agents", json={"name": "Atlas", "role": "Archivist"})
        ).json()
        relay_chat = (await c.post(f"/agents/{relay['id']}/thread")).json()["id"]
        real_model = ctx.model_client
        ctx.model_client = ScriptedModel("The vault is in Lisbon.")
        try:
            others = await other_agents(
                ctx, c.tenant, "u1", exclude=uuid.UUID(relay["id"])
            )
            tool = AskAgentTool(ctx, others, lambda t: getattr(t, "name", ""))
            result = await tool.execute(
                ctx=run_ctx(tenant=c.tenant, user="u1", thread=relay_chat),
                agent="atlas",
                request="Where is the vault?",
            )
            assert not result.is_error, result.text

            for viewed, other in ((relay, "Atlas"), (atlas, "Relay")):
                (row,) = (await c.get(f"/agents/{viewed['id']}/view/chats")).json()[
                    "items"
                ]
                assert (
                    row["kind"] == "agent"
                    and row["name"] == other
                    and row["count"] == 1
                    and row["preview"] == _line(result.text)
                )
            (row,) = (
                await c.get(
                    f"/agents/{relay['id']}/view/chats", params={"kind": "agents"}
                )
            ).json()["items"]
            said = (
                await c.get(f"/agents/{relay['id']}/view/chats/{row['key']}/messages")
            ).json()["entries"]
            assert [(e["sender"], e["text"], e["from_user"]) for e in said] == [
                ("Relay", "Where is the vault?", True),
                ("Atlas", result.text.strip(), False),
            ]

            # A plain conversation asking is the person, not another agent: nothing new in anyone's list.
            plain = (await c.post("/threads", json={"name": "plain"})).json()["id"]
            await AskAgentTool(ctx, others, lambda t: getattr(t, "name", "")).execute(
                ctx=run_ctx(tenant=c.tenant, user="u1", thread=plain),
                agent="atlas",
                request="Hello?",
            )
            assert (
                len(
                    (
                        await c.get(
                            f"/agents/{atlas['id']}/view/chats",
                            params={"kind": "agents"},
                        )
                    ).json()["items"]
                )
                == 1
            )
        finally:
            ctx.model_client = real_model
            store = ctx.runtime.store
            from sqlalchemy import text

            async with app.state.session_factory() as db:
                await db.execute(
                    text("SELECT set_config('app.bypass_rls', 'on', false)")
                )
                for (channel,) in (
                    await db.execute(
                        text(
                            "SELECT 'pair/' || id FROM agent_pairs WHERE tenant_id = :t"
                        ),
                        {"t": c.tenant},
                    )
                ).all():
                    await store.channel_delete(channel)
                for table in ("agents",):
                    await db.execute(
                        text(f"DELETE FROM {table} WHERE tenant_id = :t"),
                        {"t": c.tenant},
                    )
                await db.commit()
