"""Thread headers on both databases: ownership, ordering, cursor pages, organising, soft delete and erasure."""

from __future__ import annotations

import pytest

from loamz.agents.kernel.ports.threads import MessageNode
from loamz.agents.kernel.types.content import ChatMessage, Role, TextBlock
from loamz.agents.stores import AgentStore, ThreadHeader
from loamz.data.storage.errors import InvalidRequestError


@pytest.fixture
async def store(db):
    return AgentStore(db)


def header(tenant: str = "acme", user: str = "ann", **fields) -> ThreadHeader:
    return ThreadHeader(tenant_id=tenant, user_id=user, **fields)


async def test_a_created_thread_reads_back_and_creating_it_again_changes_nothing(store) -> None:
    made = await store.thread_headers.create(
        header(name="Plans", tags=("a", "b"), metadata={"k": 1})
    )
    again = await store.thread_headers.create(made.model_copy(update={"name": "Other"}))
    got = await store.thread_headers.get("acme", made.id)
    assert got == again
    assert (got.name, got.tags, got.metadata) == ("Plans", ("a", "b"), {"k": 1})


async def test_another_person_or_tenant_sees_nothing(store) -> None:
    mine = await store.thread_headers.create(header())
    assert await store.thread_headers.get_owned("acme", "bob", mine.id) is None
    assert await store.thread_headers.get("globex", mine.id) is None
    assert (await store.thread_headers.list("acme", "bob")).items == ()
    assert (await store.thread_headers.list("globex", "ann")).items == ()


async def test_the_list_is_pinned_first_then_latest_activity_and_pages_by_cursor(store) -> None:
    ids = [(await store.thread_headers.create(header(name=f"t{i}"))).id for i in range(5)]
    await store.thread_headers.touch("acme", ids[1])
    await store.thread_headers.update("acme", ids[3], pinned=True)
    first = await store.thread_headers.list("acme", "ann", page_size=2)
    assert [h.id for h in first.items] == [ids[3], ids[1]]
    assert first.next_page_token
    rest = await store.thread_headers.list(
        "acme", "ann", page_size=10, page_token=first.next_page_token
    )
    assert [h.id for h in rest.items] == [ids[4], ids[2], ids[0]]
    assert rest.next_page_token is None


async def test_a_foreign_page_token_is_an_invalid_request(store) -> None:
    with pytest.raises(InvalidRequestError):
        await store.thread_headers.list("acme", "ann", page_token="not-a-token")


async def test_archiving_moves_a_thread_to_the_archive_and_unpins_it(store) -> None:
    made = await store.thread_headers.create(header())
    await store.thread_headers.update("acme", made.id, pinned=True)
    archived = await store.thread_headers.update("acme", made.id, archived=True)
    assert archived.archived_at is not None and archived.pinned_at is None
    assert (await store.thread_headers.list("acme", "ann")).items == ()
    assert [
        h.id for h in (await store.thread_headers.list("acme", "ann", archived=True)).items
    ] == [made.id]


async def test_organising_is_not_activity_but_renaming_is(store) -> None:
    made = await store.thread_headers.create(header())
    pinned = await store.thread_headers.update("acme", made.id, pinned=True)
    assert pinned.updated_at == made.updated_at
    renamed = await store.thread_headers.update("acme", made.id, name="New", tags=["x"])
    assert renamed.updated_at > made.updated_at
    assert (renamed.name, renamed.tags) == ("New", ("x",))


async def test_filters_by_agent_and_tag(store) -> None:
    plain = await store.thread_headers.create(header())
    with_agent = await store.thread_headers.create(header(agent_id="scout"))
    scheduled = await store.thread_headers.create(header(tags=("scheduled_task",)))
    ids = lambda page: {h.id for h in page.items}  # noqa: E731
    assert ids(await store.thread_headers.list("acme", "ann", agent_id=None)) == {
        plain.id,
        scheduled.id,
    }
    assert ids(await store.thread_headers.list("acme", "ann", agent_id="scout")) == {with_agent.id}
    assert ids(await store.thread_headers.list("acme", "ann", without_tag="scheduled_task")) == {
        plain.id,
        with_agent.id,
    }


async def test_a_lock_keeps_its_first_reason_until_unlocked(store) -> None:
    made = await store.thread_headers.create(header())
    await store.thread_headers.lock("acme", made.id, "file gone")
    await store.thread_headers.lock("acme", made.id, "another")
    got = await store.thread_headers.get("acme", made.id)
    assert got.locked_at is not None and got.locked_reason == "file gone"
    await store.thread_headers.unlock("acme", made.id)
    assert (await store.thread_headers.get("acme", made.id)).locked_at is None


async def test_a_deleted_thread_is_gone_for_its_owner_but_kept(store) -> None:
    made = await store.thread_headers.create(header())
    assert await store.thread_headers.delete("acme", made.id) is True
    assert await store.thread_headers.delete("acme", made.id) is False
    assert await store.thread_headers.get_owned("acme", "ann", made.id) is None
    assert await store.thread_headers.update("acme", made.id, name="x") is None
    assert (
        await store.thread_headers.get("acme", made.id, include_deleted=True)
    ).deleted_at is not None
    assert await store.thread_headers.owned_ids("acme", "ann") == []
    assert await store.thread_headers.owned_ids("acme", "ann", include_deleted=True) == [made.id]


async def test_erasing_a_person_removes_their_conversations_and_nobody_elses(store) -> None:
    ann = await store.thread_headers.create(header(user="ann"))
    bob = await store.thread_headers.create(header(user="bob"))
    view = store.tenant("acme")
    for made in (ann, bob):
        node = MessageNode(
            session_id=made.id, payload=ChatMessage(role=Role.USER, content=[TextBlock(text="hi")])
        )
        await view.threads.append_and_advance(node, "main")
    erased = await view.erase(user_id="ann")
    assert erased.thread_headers == 1 and erased.thread_nodes == 1
    assert await store.thread_headers.get("acme", ann.id, include_deleted=True) is None
    assert await store.thread_headers.get("acme", bob.id) is not None
    assert (await view.threads.get_branch(bob.id, "main")).head_message_id is not None


async def test_erasing_a_tenant_leaves_other_tenants_alone(store) -> None:
    await store.thread_headers.create(header(tenant="acme"))
    kept = await store.thread_headers.create(header(tenant="globex"))
    await store.tenant("acme").tasks.create_task_list("c1", ["one"])
    erased = await store.tenant("acme").erase()
    assert erased.thread_headers == 1 and erased.task_boards == 1
    assert await store.thread_headers.get("globex", kept.id) is not None
