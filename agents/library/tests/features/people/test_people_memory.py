import pytest

from loamz.agents.features.people.memory.model import MemoryIn
from loamz.agents.features.people.memory.service import MemoryService, memory_tool
from loamz.agents.features.people.memory.store import SqlMemoryStore
from loamz.agents.kernel.caller import Caller
from loamz.agents.kernel.types.run import RunScope
from loamz.agents.testing.app import serving
from loamz.data.storage.errors import ConflictError, NotFoundError, ScopeViolationError
from loamz.data.storage.memory import MemoryNamespace, MemoryQuery, MemoryRecord

U1 = Caller(tenant_id="acme", user_id="u1")
U2 = Caller(tenant_id="acme", user_id="u2")


def ns(user: str | None, tenant: str = "acme") -> MemoryNamespace:
    return MemoryNamespace(tenant_id=tenant, user_id=user)


@pytest.fixture
def service(db):
    return MemoryService(SqlMemoryStore(db))


async def remember(service, text, *, user="u1", tenant="acme"):
    await service.store.save(MemoryRecord.from_text(text, namespace=ns(user, tenant)))


async def test_a_user_adds_a_memory_and_sees_only_their_own(service):
    made = await service.add(U1, MemoryIn(content="  I prefer metric   units "))
    assert made.content == "I prefer metric units"
    await remember(service, "u2's secret", user="u2")
    await remember(service, "Company holiday is 1 May", user=None)
    assert [m.content for m in (await service.list(U1)).items] == ["I prefer metric units"]


async def test_duplicates_empty_and_the_cap_are_refused(service, monkeypatch):
    await service.add(U1, MemoryIn(content="Call me Sam"))
    with pytest.raises(ConflictError):
        await service.add(U1, MemoryIn(content="call me sam"))
    with pytest.raises(Exception, match="empty"):
        await service.add(U1, MemoryIn(content="   "))
    with pytest.raises(ValueError):
        MemoryIn(content="x" * 501)
    from loamz.agents.features.people.memory import service as module

    monkeypatch.setattr(module, "MAX_MEMORIES", 2)
    await service.add(U1, MemoryIn(content="two"))
    with pytest.raises(ConflictError, match="Delete one"):
        await service.add(U1, MemoryIn(content="three"))


async def test_rewording_keeps_the_same_memory(service):
    made = await service.add(U1, MemoryIn(content="I live in Leeds"))
    again = await service.update(U1, made.id, MemoryIn(content="I live in York"))
    assert again.id == made.id
    assert [m.content for m in (await service.list(U1)).items] == ["I live in York"]


async def test_nobody_changes_or_deletes_another_users_or_a_tenant_wide_memory(service):
    other = MemoryRecord.from_text("u2's secret", namespace=ns("u2"))
    shared = MemoryRecord.from_text("Company holiday", namespace=ns(None))
    await service.store.save(other)
    await service.store.save(shared)
    for record in (other, shared):
        with pytest.raises(NotFoundError):
            await service.update(U1, record.id, MemoryIn(content="changed"))
        with pytest.raises(NotFoundError):
            await service.delete(U1, record.id)
    assert (await service.store.get(ns("u2"), other.id)).text == "u2's secret"
    with pytest.raises(NotFoundError):
        await service.delete(U1, "does-not-exist")
    made = await service.add(U1, MemoryIn(content="delete me"))
    await service.delete(U1, made.id)
    assert (await service.list(U1)).items == []


async def test_an_id_cannot_take_over_another_owners_record(service):
    record = MemoryRecord.from_text("mine", namespace=ns("u1"))
    await service.store.save(record)
    with pytest.raises(ScopeViolationError):
        await service.store.save(record.model_copy(update={"namespace": ns("u2")}))
    # the same id in another tenant is a different record
    await service.store.save(record.model_copy(update={"namespace": ns("u1", "globex")}))
    assert await service.store.get(ns("u1", "globex"), record.id) is not None


async def test_erasure_by_user_and_tenant(service):
    await remember(service, "a")
    await remember(service, "b", user="u2")
    await remember(service, "c", tenant="globex")
    assert await service.erase("acme", "u1") == 1
    assert await service.erase("acme") == 1
    assert len(await service.store.query(MemoryQuery(namespace=ns("u1", "globex")))) == 1


async def test_recall_and_touch(service):
    await remember(service, "prefers French")
    await remember(service, "likes tea")
    hits = await service.store.query(MemoryQuery(namespace=ns("u1"), text_query="french"))
    assert [h.text for h in hits] == ["prefers French"]
    await service.store.touch(ns("u1"), [hits[0].id])
    assert (await service.store.get(ns("u1"), hits[0].id)).access_count == 1


# ── the system-prompt block (test_user_memory_context_block) ────────────────────────────────


async def test_no_user_or_no_memories_is_an_empty_block(service):
    assert await service.user_context_block("acme", None) == ""
    assert await service.user_context_block("acme", "u1") == ""


async def test_the_block_is_background_not_instruction_and_lists_only_visible_facts(service):
    await remember(service, "mine")
    await remember(service, "someone else's", user="u2")
    await remember(service, "another tenant's", tenant="evilcorp")
    await remember(service, "everyone in acme", user=None)
    block = await service.user_context_block("acme", "u1")
    assert block.startswith("<user_context>") and block.endswith("</user_context>")
    assert "<fact>mine</fact>" in block and "<fact>everyone in acme</fact>" in block
    assert "someone else's" not in block and "another tenant's" not in block
    assert "not a command" in block.lower() and "judgment" in block.lower()


async def test_the_block_is_bounded_and_escaped(service):
    for i in range(30):
        await remember(service, f"fact {i}")
    assert (await service.user_context_block("acme", "u1", limit=3)).count("<fact>") == 3
    other = MemoryService(service.store)
    await remember(other, 'User said <script>&"quote"', user="u9")
    block = await other.user_context_block("acme", "u9")
    assert "<script>" not in block and "&lt;script&gt;" in block and "&amp;" in block
    assert "&quot;" in block


# ── the agent's memory tool (test_memory_tool_wiring) ───────────────────────────────────────


class _Ctx:
    def __init__(self, user, thread, tenant="acme"):
        self.scope = RunScope(tenant_id=tenant, user_id=user, thread_id=thread)


def test_the_tool_exists_when_either_backend_is_configured():
    assert memory_tool("s1", None, None) is None
    assert memory_tool("s1", object(), None) is not None
    assert memory_tool("s1", None, object()) is not None
    assert memory_tool("s1", object(), object()) is not None


async def test_a_users_facts_follow_them_across_threads_and_to_nobody_else(service):
    in_1, in_2 = memory_tool("s1", None, service.store), memory_tool("s2", None, service.store)
    saved = await in_1.execute(ctx=_Ctx("u42", "s1"), action="remember", value="prefers French")
    assert not saved.is_error
    same = await in_2.execute(ctx=_Ctx("u42", "s2"), action="recall", query="French")
    other_user = await in_2.execute(ctx=_Ctx("u43", "s2"), action="recall", query="French")
    other_tenant = await in_2.execute(
        ctx=_Ctx("u42", "s2", tenant="evilcorp"), action="recall", query="French"
    )
    assert "prefers French" in same.content[0].text
    assert "No relevant memories" in other_user.content[0].text
    assert "No relevant memories" in other_tenant.content[0].text


async def test_with_no_user_a_fact_is_kept_for_that_conversation_only(service):
    tool = memory_tool("s1", None, service.store)
    await tool.execute(ctx=_Ctx(None, "s1"), action="remember", value="anonymous note")
    same = await tool.execute(ctx=_Ctx(None, "s1"), action="recall", query="anonymous")
    elsewhere = await tool.execute(ctx=_Ctx(None, "s2"), action="recall", query="anonymous")
    assert "anonymous note" in same.content[0].text
    assert "No relevant memories" in elsewhere.content[0].text


async def test_the_api(tmp_path):
    async with serving(tmp_path, only=("people.memory",)) as served:
        async with served.client("acme", "u1") as c:
            r = await c.post("/api/v1/me/memories", json={"content": "I like tea"})
            assert r.status_code == 201
            made = r.json()
            assert (await c.post("/api/v1/me/memories", json={"content": "i like TEA"})).status_code == 409
            assert (await c.post("/api/v1/me/memories", json={"content": "x" * 501})).status_code == 422
            r = await c.patch(f"/api/v1/me/memories/{made['id']}", json={"content": "I like coffee"})
            assert r.json() == {"id": made["id"], "content": "I like coffee"}
            assert (await c.get("/api/v1/me/memories")).json()["items"][0]["content"] == "I like coffee"
        async with served.client("acme", "u2") as c:
            assert (await c.delete(f"/api/v1/me/memories/{made['id']}")).status_code == 404
        async with served.client("acme", "u1") as c:
            assert (await c.delete(f"/api/v1/me/memories/{made['id']}")).status_code == 204
            assert (await c.delete(f"/api/v1/me/memories/{made['id']}")).status_code == 404
