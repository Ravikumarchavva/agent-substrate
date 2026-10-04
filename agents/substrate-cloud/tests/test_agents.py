"""Agent profiles: owned by one user, they set a conversation's role, tool ceiling and persistent workspace; the workspace is shared by all of
the agent's conversations and no one else can open it."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from substrate.types import RunScope
from substrate_cloud.monolith.models import Agent
from substrate_cloud.monolith.services.agent_service import (
    agent_instructions_block,
    narrow_tools,
    workspace_id_for,
)

from test_scheduled_notifications import session


def agent(**kw):
    return Agent(id=uuid.uuid4(), tenant_id="t", user_identifier="u", name="Scout", role="Researcher", instructions="Cite sources.", **kw)


def test_an_agent_workspace_id_is_its_own_and_a_plain_chat_keeps_the_threads():
    a = agent()
    thread = SimpleNamespace(id=uuid.uuid4())
    assert workspace_id_for(thread, a) == f"dot-{a.id}"
    assert workspace_id_for(thread, None) == str(thread.id)


def test_the_scope_keys_files_by_the_workspace_when_there_is_one():
    plain = RunScope(thread_id="t1")
    assert plain.workspace == "t1"
    with_agent = RunScope.from_metadata({"workspace_id": "dot-9", "thread_id": "t1"}, thread_id="t1", agent_id="a", agent_label="a")
    assert with_agent.workspace == "dot-9" and with_agent.thread_id == "t1"
    assert with_agent.child_metadata()["workspace_id"] == "dot-9"  # a sub-agent works in the same workspace


def test_the_tool_list_is_a_ceiling():
    tools = [SimpleNamespace(n="calculator"), SimpleNamespace(n="web_search"), SimpleNamespace(n="send_email")]
    name = lambda t: t.n  # noqa: E731
    assert narrow_tools(tools, agent(allowed_tools=None), name) == tools
    assert [t.n for t in narrow_tools(tools, agent(allowed_tools=["calculator"]), name)] == ["calculator"]
    assert narrow_tools(tools, agent(allowed_tools=[]), name) == []


def test_the_model_is_told_who_it_is():
    block = agent_instructions_block(agent())
    assert "Scout" in block and "Researcher" in block and "Cite sources." in block


@pytest.mark.requires_postgres
async def test_agents_are_private_validated_and_start_conversations():
    async with session() as c:
        assert (await c.post("/agents", json={"name": ""})).status_code == 422
        assert (await c.post("/agents", json={"name": "X", "allowed_tools": ["no_such_tool"]})).status_code == 422
        made = (await c.post("/agents", json={"name": "Scout", "role": "Researcher", "instructions": "Be brief."})).json()
        assert made["workspace_id"] == f"dot-{made['id']}" and made["allowed_tools"] is None
        assert [a["id"] for a in (await c.get("/agents")).json()] == [made["id"]]

        tools = (await c.get("/agents/tools")).json()
        assert tools  # the picker has something to offer
        pick = tools[0]["name"]
        patched = (await c.patch(f"/agents/{made['id']}", json={"allowed_tools": [pick]})).json()
        assert patched["allowed_tools"] == [pick]
        assert (await c.patch(f"/agents/{made['id']}", json={"all_tools": True})).json()["allowed_tools"] is None

        thread = (await c.post("/threads", json={"name": "t", "agent_id": made["id"]})).json()
        assert thread["agent_id"] == made["id"]
        assert (await c.post("/threads", json={"name": "t", "agent_id": str(uuid.uuid4())})).status_code == 404

        # its workspace is addressable only by its owner
        assert (await c.get("/workspace/file", params={"thread_id": made["workspace_id"], "path": "notes.md"})).status_code == 404  # no such file, but not 403/500
        assert (await c.get("/workspace/file", params={"thread_id": f"dot-{uuid.uuid4()}", "path": "x"})).status_code == 404

        # a file in the agent's workspace is listed under the agent's name, and goes with the agent
        put = await c.put("/workspace/file", params={"thread_id": made["workspace_id"], "path": "notes.md"}, content=b"# notes")
        assert put.status_code == 200, put.text
        listed = [f for f in (await c.get("/workspace/files")).json()["files"] if f["session_id"] == made["workspace_id"]]
        assert [(f["name"], f["session_name"]) for f in listed] == [("notes.md", "Scout")]
        assert (await c.get("/workspace/file", params={"thread_id": made["workspace_id"], "path": "notes.md"})).content == b"# notes"

        assert (await c.delete(f"/agents/{made['id']}")).status_code == 204
        assert (await c.get(f"/agents/{made['id']}")).status_code == 404
        # the conversation stays, as an ordinary one
        assert (await c.get(f"/threads/{thread['id']}")).json()["agent_id"] is None
        assert not [f for f in (await c.get("/workspace/files")).json()["files"] if f["session_id"] == made["workspace_id"]]
