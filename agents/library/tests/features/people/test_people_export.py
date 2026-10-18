import json

from loamz.agents.features.people.memory.model import MemoryIn
from loamz.agents.features.people.memory.service import memory
from loamz.agents.features.people.notifications.service import notifications
from loamz.agents.features.people.preferences.model import Preferences
from loamz.agents.features.people.preferences.service import preferences
from loamz.agents.kernel.caller import Caller
from loamz.agents.kernel.ports.threads import MessageNode
from loamz.agents.kernel.types.content import ChatMessage, Role, TextBlock
from loamz.agents.stores.headers import ThreadHeader
from loamz.agents.testing.app import serving

ALICE = Caller(tenant_id="acme", user_id="alice")


async def test_the_export_holds_the_callers_data_and_only_theirs(tmp_path):
    async with serving(tmp_path, only=("people.",)) as served:
        c = served.container
        await c.get(preferences).replace(ALICE, Preferences(timezone="Asia/Kolkata"))
        await c.get(memory).add(ALICE, MemoryIn(content="I like tea"))
        await c.get(memory).add(Caller(tenant_id="acme", user_id="bob"), MemoryIn(content="bob's"))
        await c.get(notifications).notify("acme", "alice", "Done", "ok")
        store = c.agent_store
        await store.thread_headers.create(
            ThreadHeader(id="t1", tenant_id="acme", user_id="alice", name="Plans")
        )
        await store.thread_headers.create(ThreadHeader(id="t2", tenant_id="acme", user_id="bob"))
        threads = store.tenant("acme").threads
        await threads.ensure_branch("t1", "main")
        for role, text in ((Role.USER, "hello"), (Role.ASSISTANT, "hi there")):
            branch = await threads.get_branch("t1", "main")
            await threads.append_and_advance(
                MessageNode(
                    session_id="t1",
                    parent_id=branch.head_message_id,
                    payload=ChatMessage(role=role, content=[TextBlock(text=text)]),
                ),
                "main",
            )
        async with served.client("acme", "alice") as client:
            response = await client.get("/api/v1/me/export")
            assert response.status_code == 200
            assert "my-data.json" in response.headers["content-disposition"]
            doc = json.loads(response.text)
            assert doc["preferences"]["timezone"] == "Asia/Kolkata"
            assert doc["memories"] == ["I like tea"]
            assert [n["title"] for n in doc["notifications"]] == ["Done"]
            assert [x["title"] for x in doc["conversations"]] == ["Plans"]
            assert [m["text"] for m in doc["conversations"][0]["messages"]] == ["hello", "hi there"]
            assert "bob" not in response.text
            again = await client.get("/api/v1/me/export")  # once a minute is enough
            assert again.status_code == 429 and again.headers["retry-after"]
