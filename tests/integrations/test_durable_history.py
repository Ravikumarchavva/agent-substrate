from __future__ import annotations

import os
import pytest
from sqlalchemy.exc import OperationalError

from substrate.capabilities.history import DurableHistoryProvider
from substrate.kernel import Actor, ChatMessage
from substrate.kernel.core.content import TextBlock

pytestmark = [pytest.mark.requires_postgres]


def test_postgres_history_internal_key_fits_legacy_column() -> None:
    """A conversation actor's key is its session_id (see identity.py's
    ``Actor`` docstring) -- realistic overflow is a long ``type:key`` pair,
    not a separate session_id the key already made redundant."""
    provider = DurableHistoryProvider("postgresql+asyncpg://user:pass@localhost/db")
    agent_id = Actor(type="conversation", key="session-" + ("y" * 120))

    storage_key = provider._session_key(agent_id, agent_id.key)

    assert len(storage_key) <= 128
    assert storage_key.startswith("h:")


@pytest.mark.asyncio
async def test_postgres_history_provider():
    # Fallback to local dev postgres db url if environment is not set
    db_url = os.getenv(
        "DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/agentdb"
    )
    provider = DurableHistoryProvider(db_url, echo=False)

    try:
        await provider.connect()
    except (OperationalError, Exception) as e:
        pytest.skip(f"PostgreSQL database not available: {e}")

    agent_id = Actor(type="agent", key="test-agent")
    session_id = "test-session-456"

    try:
        # Write clean state using protocol methods
        await provider.clear(agent_id, session_id=session_id)
        assert await provider.count_messages(agent_id, session_id=session_id) == 0

        # Save a message via the protocol
        msg = ChatMessage(role="user", content=[TextBlock(text="postgres message")])
        await provider.append(agent_id, msg, session_id=session_id, run_id="r1")

        # Load via the protocol
        loaded = await provider.get_messages(agent_id, session_id=session_id)
        assert len(loaded) == 1
        assert loaded[0].role == "user"
        assert loaded[0].content[0].text == "postgres message"  # type: ignore[union-attr]

        # Count via the protocol
        assert await provider.count_messages(agent_id, session_id=session_id) == 1

        # Cleanup via the protocol
        await provider.clear(agent_id, session_id=session_id)
        assert await provider.count_messages(agent_id, session_id=session_id) == 0
    finally:
        await provider.disconnect()


@pytest.mark.asyncio
async def test_postgres_history_dag_node_append_and_retrieval():
    db_url = os.getenv(
        "DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/agentdb"
    )
    provider = DurableHistoryProvider(db_url, echo=False)
    try:
        await provider.connect()
    except (OperationalError, Exception) as e:
        pytest.skip(f"PostgreSQL database not available: {e}")

    session_id = "test-dag-sess-1"
    try:
        await provider.clear_session_dag(session_id)

        from substrate.kernel.exceptions import DAGIntegrityError
        from substrate.kernel.storage.history import MessageNode

        # 1. Root node append
        root = MessageNode(
            id="node-pg-root",
            parent_id=None,
            session_id=session_id,
            run_id="run-1",
            payload=ChatMessage(role="user", content=[TextBlock(text="Root prompt")]),
        )
        await provider.append_node(root)

        fetched = await provider.get_node("node-pg-root")
        assert fetched is not None
        assert fetched.id == "node-pg-root"
        assert fetched.parent_id is None
        assert fetched.session_id == session_id
        assert fetched.run_id == "run-1"
        assert fetched.payload.text == "Root prompt"

        # 2. Idempotent re-append
        await provider.append_node(root)

        # 3. Conflicting duplicate append
        conflict = MessageNode(
            id="node-pg-root",
            parent_id=None,
            session_id=session_id,
            run_id="run-1",
            payload=ChatMessage(role="assistant", content=[TextBlock(text="Altered")]),
        )
        with pytest.raises(DAGIntegrityError):
            await provider.append_node(conflict)

        # 4. Self-loop
        self_loop = MessageNode(
            id="node-pg-self",
            parent_id="node-pg-self",
            session_id=session_id,
            payload=ChatMessage(role="user", content=[TextBlock(text="Self")]),
        )
        with pytest.raises(DAGIntegrityError):
            await provider.append_node(self_loop)

        # 5. Non-existent parent
        orphan = MessageNode(
            id="node-pg-orphan",
            parent_id="does-not-exist",
            session_id=session_id,
            payload=ChatMessage(role="user", content=[TextBlock(text="Orphan")]),
        )
        with pytest.raises(DAGIntegrityError):
            await provider.append_node(orphan)

        # 6. Child node append
        child = MessageNode(
            id="node-pg-child",
            parent_id="node-pg-root",
            session_id=session_id,
            payload=ChatMessage(role="assistant", content=[TextBlock(text="Child answer")]),
        )
        await provider.append_node(child)
        assert await provider.get_node("node-pg-child") is not None
    finally:
        await provider.clear_session_dag(session_id)
        await provider.disconnect()


@pytest.mark.asyncio
async def test_postgres_history_dag_append_and_advance_and_cas():
    db_url = os.getenv(
        "DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/agentdb"
    )
    provider = DurableHistoryProvider(db_url, echo=False)
    try:
        await provider.connect()
    except (OperationalError, Exception) as e:
        pytest.skip(f"PostgreSQL database not available: {e}")

    session_id = "test-dag-sess-cas"
    try:
        await provider.clear_session_dag(session_id)

        from substrate.kernel.exceptions import BranchHeadConflictError
        from substrate.kernel.storage.history import MessageNode

        # 1. Advance on empty branch
        n1 = MessageNode(
            id="n1",
            parent_id=None,
            session_id=session_id,
            payload=ChatMessage(role="user", content=[TextBlock(text="First")]),
        )
        b1 = await provider.append_and_advance(n1, "main", expected_head_id=None)
        assert b1.head_message_id == "n1"
        assert b1.version == 1

        # 2. Advance with wrong expected_head_id
        n2 = MessageNode(
            id="n2",
            parent_id="n1",
            session_id=session_id,
            payload=ChatMessage(role="assistant", content=[TextBlock(text="Second")]),
        )
        with pytest.raises(BranchHeadConflictError):
            await provider.append_and_advance(n2, "main", expected_head_id="wrong-head")

        # 3. Advance with wrong version
        with pytest.raises(BranchHeadConflictError):
            await provider.append_and_advance(n2, "main", expected_version=99)

        # 4. Advance with wrong parent_id
        n_wrong_parent = MessageNode(
            id="n_wrong",
            parent_id=None,  # but head is n1!
            session_id=session_id,
            payload=ChatMessage(role="user", content=[TextBlock(text="Bad")]),
        )
        with pytest.raises(BranchHeadConflictError):
            await provider.append_and_advance(n_wrong_parent, "main")

        # 5. Successful advance
        b2 = await provider.append_and_advance(n2, "main", expected_head_id="n1", expected_version=1)
        assert b2.head_message_id == "n2"
        assert b2.version == 2
    finally:
        await provider.clear_session_dag(session_id)
        await provider.disconnect()


@pytest.mark.asyncio
async def test_postgres_history_dag_forking_and_checkpoints():
    db_url = os.getenv(
        "DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/agentdb"
    )
    provider = DurableHistoryProvider(db_url, echo=False)
    try:
        await provider.connect()
    except (OperationalError, Exception) as e:
        pytest.skip(f"PostgreSQL database not available: {e}")

    session_id = "test-dag-sess-fork"
    try:
        await provider.clear_session_dag(session_id)

        from substrate.agents.context.history import AncestryCheckpointResolver, DefaultHistoryResolver
        from substrate.kernel.exceptions import BranchAlreadyExistsError, DAGIntegrityError
        from substrate.kernel.storage.history import HistoryCheckpoint, MessageNode

        # Setup 3-node chain: root -> middle -> leaf
        n1 = MessageNode(
            id="node-1",
            parent_id=None,
            session_id=session_id,
            payload=ChatMessage(role="user", content=[TextBlock(text="Msg 1")]),
        )
        await provider.append_and_advance(n1, "main")

        n2 = MessageNode(
            id="node-2",
            parent_id="node-1",
            session_id=session_id,
            payload=ChatMessage(role="assistant", content=[TextBlock(text="Msg 2")]),
        )
        await provider.append_and_advance(n2, "main")

        n3 = MessageNode(
            id="node-3",
            parent_id="node-2",
            session_id=session_id,
            payload=ChatMessage(role="user", content=[TextBlock(text="Msg 3")]),
        )
        await provider.append_and_advance(n3, "main")

        # Fork branch from middle node
        fork_b = await provider.fork_branch(
            session_id,
            source_branch_id="main",
            new_branch_id="experiment",
            fork_from_message_id="node-2",
        )
        assert fork_b.id == "experiment"
        assert fork_b.head_message_id == "node-2"
        assert fork_b.forked_from_message_id == "node-2"

        # Duplicate fork rejected
        with pytest.raises(BranchAlreadyExistsError):
            await provider.fork_branch(session_id, "main", "experiment")

        # Ancestry resolution
        resolver = DefaultHistoryResolver(provider)
        chain = await resolver.resolve_ancestry("node-3")
        assert [n.id for n in chain] == ["node-1", "node-2", "node-3"]

        # Checkpoints
        cp1 = HistoryCheckpoint(
            id="cp-1",
            session_id=session_id,
            anchor_message_id="node-2",
            summary="Summary up to node 2",
        )
        await provider.save_checkpoint(cp1)

        fetched_cp = await provider.get_checkpoint("cp-1")
        assert fetched_cp is not None
        assert fetched_cp.summary == "Summary up to node 2"

        cp_resolver = AncestryCheckpointResolver(provider)
        applicable = await cp_resolver.find_applicable_checkpoint("node-3")
        assert applicable is not None
        assert applicable.id == "cp-1"
    finally:
        await provider.clear_session_dag(session_id)
        await provider.disconnect()

