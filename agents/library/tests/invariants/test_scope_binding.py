"""Invariant register — store handles are bound to a scope (rows I1, I2, I3).

A bound handle can only see its tenant. Two things are checked for every port:

* it still *is* the port: the shared conformance suite runs through a bound handle, so binding costs
  nothing a caller relied on;
* it is a wall: what one tenant stores is invisible to another through any route, including by id, and no
  name a caller chooses — a hostile session, conversation, key — reaches outside the tenant.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from tests._stores import agent_store_at

from loamz.agents.kernel.ports.graph import Entity, Relationship
from loamz.agents.kernel.ports.threads import HistoryCheckpoint, MessageNode
from loamz.agents.kernel.types.content import ChatMessage, Role
from loamz.agents.kernel.types.scope import Scope
from loamz.agents.stores import AgentStore
from loamz.agents.stores.scoped import bind_graph, bind_tasks, bind_threads
from loamz.agents.testing.conformance.graph_store import GraphStoreConformance
from loamz.agents.testing.conformance.short_term_memory import ShortTermMemoryConformance
from loamz.agents.testing.conformance.task_store import TaskStoreConformance
from loamz.agents.testing.conformance.thread_store import ThreadStoreConformance
from loamz.data.storage.errors import InvalidRequestError

A, B = Scope(tenant_id="acme"), Scope(tenant_id="evilcorp")


def test_a_scope_needs_a_tenant() -> None:
    with pytest.raises(ValidationError):
        Scope(tenant_id="")
    with pytest.raises(ValidationError):
        Scope(tenant_id="   ")
    assert Scope.of(type("R", (), {"tenant_id": None})()).tenant_id == "default"


# ------------------------------------------------------------------ bound handles still conform


class TestBoundHistoryConforms(ThreadStoreConformance):
    @pytest.fixture
    async def store(self, tmp_path):
        return agent_store_at(tmp_path / "threads").tenant(A).threads


class TestBoundTasksConform(TaskStoreConformance):
    @pytest.fixture
    async def store(self, tmp_path):
        return agent_store_at(tmp_path).tenant(A).tasks


class TestBoundSessionStateConforms(ShortTermMemoryConformance):
    @pytest.fixture
    async def store(self, tmp_path):
        return agent_store_at(tmp_path).tenant(A).session_state


class TestBoundGraphConforms(GraphStoreConformance):
    @pytest.fixture
    async def store(self, tmp_path):
        return agent_store_at(tmp_path).tenant(A).graph


# ------------------------------------------------------------------ and they are walls


def _node(session: str, text: str, parent: MessageNode | None = None) -> MessageNode:
    return MessageNode(
        session_id=session,
        parent_id=parent.id if parent else None,
        payload=ChatMessage(role=Role.USER, content=text),
    )


async def test_i03_history_of_one_tenant_is_invisible_to_another(tmp_path) -> None:
    raw = agent_store_at(tmp_path / "threads").threads
    mine, theirs = bind_threads(raw, A), bind_threads(raw, B)
    n = _node("s", "secret")
    await mine.append_node(n)
    await mine.ensure_branch("s", "main")
    await mine.append_and_advance(n, "main")
    await mine.save_checkpoint(
        HistoryCheckpoint(session_id="s", anchor_message_id=n.id, summary="x")
    )
    cp = (await mine.list_checkpoints("s"))[0]

    assert await theirs.get_node(n.id) is None, "a node was readable by id from another tenant"
    assert await theirs.get_checkpoint(cp.id) is None
    assert await theirs.get_branch("s", "main") is None and await theirs.list_branches("s") == []
    with pytest.raises(ValueError):
        await theirs.set_branch_head("s", "main", n.id)
    with pytest.raises(ValueError):
        await theirs.save_checkpoint(
            HistoryCheckpoint(session_id="s", anchor_message_id=n.id, summary="steal")
        )
    with pytest.raises(ValueError):
        await theirs.append_node(_node("s", "child of a stranger", n))
    await theirs.delete_session("s")
    assert await mine.get_node(n.id) is not None, "another tenant deleted this tenant's session"


async def test_i03_ids_returned_to_the_caller_carry_no_tenant_prefix(tmp_path) -> None:
    h = agent_store_at(tmp_path / "threads").tenant(A).threads
    n = _node("s", "x")
    await h.append_node(n)
    assert (await h.get_node(n.id)).session_id == "s"


async def test_i03_graph_namespaces_cannot_be_escaped(tmp_path) -> None:
    raw = agent_store_at(tmp_path).graph
    mine, theirs = bind_graph(raw, A), bind_graph(raw, B)
    await mine.add_entities([Entity(id="a", label="P"), Entity(id="b", label="P")])
    await mine.add_relationships([Relationship(id="r", source_id="a", target_id="b", type="K")])
    assert (await theirs.get_neighbors("a")).entities == ()
    assert await theirs.delete_entity("a") is False
    # Passing a namespace subdivides the tenant; it cannot name another tenant's.
    assert (await theirs.get_neighbors("a", namespace="acme")).entities == ()
    assert "b" in {e.id for e in (await mine.get_neighbors("a")).entities}


async def test_i03_tasks_of_another_tenant_cannot_be_touched_by_board_id(
    tmp_path,
) -> None:
    raw = agent_store_at(tmp_path).tasks
    mine, theirs = bind_tasks(raw, A), bind_tasks(raw, B)
    board = await mine.create_task_list("c", ["t"])
    tid = board.tasks[0].id
    assert board.conversation_id == "c"
    assert await theirs.get_task_list(board.id) is None
    assert await theirs.update_status(board.id, tid, "failed") is None  # type: ignore[arg-type]
    assert (
        await theirs.add_tasks(board.id, ["x"]) == []
        and await theirs.delete_task(board.id, tid) is False
    )
    assert (
        await theirs.increment_retry(board.id, tid) is None
        and await theirs.force_retry(board.id, tid) is None
    )
    assert await theirs.update_task_title(board.id, tid, "hijacked") is None
    assert (await mine.get_task_list(board.id)).tasks[0].title == "t"


async def test_i03_a_tenant_whose_name_looks_like_another_tenants_prefix_gets_its_own_wall(
    tmp_path,
) -> None:
    raw = agent_store_at(tmp_path / "threads").threads
    plain, tricky = (
        bind_threads(raw, Scope(tenant_id="a")),
        bind_threads(raw, Scope(tenant_id="a/b")),
    )
    n = _node("s", "x")
    await plain.append_node(n)
    assert await tricky.get_node(n.id) is None
    n2 = _node("b/s", "y")
    await plain.append_node(n2)
    assert await tricky.get_node(n2.id) is None, (
        "tenant 'a' session 'b/s' was readable as tenant 'a/b' session 's'"
    )


async def test_i03_a_fenced_store_keeps_absolute_keys_but_only_inside_its_tenant(
    tmp_path,
) -> None:
    from loamz.agents.stores.scoped import fence_objects

    raw = agent_store_at(tmp_path).files
    mine = fence_objects(raw, A)
    await mine.upload("tenants/acme/users/u/uploads/a.bin", b"ok")
    await raw.upload("tenants/evilcorp/secret", b"keep me")
    assert await mine.download("tenants/acme/users/u/uploads/a.bin") == b"ok"
    assert [k for k, _s, _m in await mine.list_prefix("tenants/acme/users/u/")] == [
        "tenants/acme/users/u/uploads/a.bin"
    ]
    hostile = [
        "tenants/evilcorp/secret",
        "tenants/acme/../evilcorp/secret",
        "tenants/acme/users/../../../evilcorp/secret",
        "tenants/acmeevil/x",
        "tenants/acme",
        "/tenants/acme/x",
        "tenants\\acme\\x",
        "other/x",
    ]
    for key in hostile:
        with pytest.raises(InvalidRequestError):
            await mine.download(key)
        with pytest.raises(InvalidRequestError):
            await mine.upload(key, b"attack")
    for prefix in ("tenants/evilcorp/", "tenants/acme/../evilcorp/", "tenants/"):
        with pytest.raises(InvalidRequestError):
            await mine.delete_prefix(prefix)
        with pytest.raises(InvalidRequestError):
            await mine.list_prefix(prefix)
    with pytest.raises(InvalidRequestError):
        await mine.copy_prefix("tenants/acme/", "tenants/evilcorp/stolen/")
    with pytest.raises(InvalidRequestError):
        await mine.usage_bytes("evilcorp")
    with pytest.raises(InvalidRequestError):
        fence_objects(raw, Scope(tenant_id="a/b"))
    assert await raw.download("tenants/evilcorp/secret") == b"keep me"


# ------------------------------------------------------------------ erasing a tenant


async def _fill(store: AgentStore, scope: Scope, word: str) -> None:
    tenant = store.tenant(scope)
    n = _node("s", word)
    await tenant.threads.append_node(n)
    await tenant.threads.ensure_branch("s", "main")
    await tenant.threads.append_and_advance(n, "main")
    await tenant.tasks.create_task_list("c", [word])
    await tenant.graph.add_entities([Entity(id="e", label="P", name=word)])
    await tenant.session_state.set_state("s", {"note": word})


async def test_i04_erasing_a_tenant_reaches_every_facet_and_no_other_tenant(
    tmp_path,
) -> None:
    store = agent_store_at(tmp_path)
    await _fill(store, A, "alpha")
    await _fill(store, B, "bravo")

    erased = await store.tenant(A).erase()

    assert erased.thread_nodes == erased.task_boards == erased.graph_entities == 1
    assert erased.session_states == 1
    mine, theirs = store.tenant(A), store.tenant(B)
    assert (
        await mine.threads.list_branches("s") == []
        and await mine.tasks.get_by_conversation("c") is None
    )
    assert (await mine.graph.get_neighbors("e")).entities == ()
    assert await mine.session_state.get_state("s") == {}
    # the other tenant lost nothing
    assert (
        len(await theirs.threads.list_branches("s")) == 1
        and await theirs.tasks.get_by_conversation("c") is not None
    )
    assert len((await theirs.graph.get_neighbors("e")).entities) == 1
    assert await theirs.session_state.get_state("s") == {"note": "bravo"}


async def test_i04_an_erased_tenants_words_are_gone_from_the_database_file(
    tmp_path,
) -> None:
    store = agent_store_at(tmp_path)
    await _fill(store, A, "zanzibarquartz")
    await store.tenant(A).erase()
    await store.aclose()
    for path in (tmp_path / "agents.db", tmp_path / "agents.db-wal"):
        if path.exists():
            assert b"zanzibarquartz" not in path.read_bytes()


async def test_i04_erasing_a_tenant_whose_name_is_a_prefix_of_another_leaves_the_other_alone(
    tmp_path,
) -> None:
    store = agent_store_at(tmp_path)
    short, long = Scope(tenant_id="acme"), Scope(tenant_id="acme-corp")
    for scope in (short, long):
        await store.tenant(scope).tasks.create_task_list("c", ["t"])
    await store.tenant(short).erase()
    assert await store.tenant(long).tasks.get_by_conversation("c") is not None


async def test_i04_erasing_one_conversation_leaves_its_neighbours(tmp_path) -> None:
    store = agent_store_at(tmp_path)
    tenant = store.tenant(A)
    for conversation in ("c1", "c10"):
        n = _node(conversation, "x")
        await tenant.threads.append_node(n)
        await tenant.tasks.create_task_list(conversation, ["t"])
    erased = await tenant.erase_conversation("c1")
    assert erased.thread_nodes == erased.task_boards == 1
    assert await tenant.tasks.get_by_conversation("c1") is None
    assert await tenant.tasks.get_by_conversation("c10") is not None
