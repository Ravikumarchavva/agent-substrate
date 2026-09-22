from __future__ import annotations

import os
import pytest
from sqlalchemy.exc import OperationalError

from substrate.integrations.history import DurableHistoryProvider
from substrate.kernel import ChatMessage
from substrate.kernel.core.content import TextBlock

pytestmark = [pytest.mark.requires_postgres]


@pytest.mark.asyncio
async def test_legacy_linear_sessions_are_chained_into_the_dag_on_connect():
    """Pre-DAG chats lived in history_messages; connect() must not strand them."""
    from sqlalchemy import delete

    from substrate.agents.storage.history import project_messages
    from substrate.integrations.history.durable_history import (
        HistoryMessage,
        HistorySession,
        serialize_message,
    )

    db_url = os.getenv(
        "DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/agentdb"
    )
    provider = DurableHistoryProvider(db_url, echo=False)
    try:
        await provider.connect()
    except (OperationalError, Exception) as e:
        pytest.skip(f"PostgreSQL database not available: {e}")

    session_id = "legacy-migrate-test"
    storage_key = f"assistant:{session_id}"  # legacy "<agent.type>:<agent.key>"
    factory = provider._get_session()
    try:
        await provider.delete_session(session_id)
        async with factory() as db:
            db.add(HistorySession(id=storage_key, message_count=2))
            for seq, (role, text) in enumerate([("user", "old q"), ("assistant", "old a")], 1):
                db.add(
                    HistoryMessage(
                        session_id=storage_key,
                        sequence=seq,
                        message_type=role,
                        payload=serialize_message(
                            ChatMessage(role=role, content=[TextBlock(text=text)])
                        ),
                        run_id="r0",
                    )
                )
            await db.commit()

        assert await provider._migrate_legacy_messages() >= 1

        msgs = await project_messages(provider, session_id)
        assert [m.content[0].text for m in msgs] == ["old q", "old a"]  # type: ignore[union-attr]
        # Idempotent: the legacy rows are gone, a second pass migrates nothing new.
        async with factory() as db:
            left = (
                await db.execute(
                    HistoryMessage.__table__.select().where(
                        HistoryMessage.session_id == storage_key
                    )
                )
            ).all()
        assert left == []
    finally:
        await provider.delete_session(session_id)
        async with factory() as db:
            await db.execute(
                delete(HistoryMessage).where(HistoryMessage.session_id == storage_key)
            )
            await db.execute(delete(HistorySession).where(HistorySession.id == storage_key))
            await db.commit()
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
        await provider.delete_session(session_id)

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
        await provider.delete_session(session_id)
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
        await provider.delete_session(session_id)

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
        await provider.delete_session(session_id)
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
        await provider.delete_session(session_id)

        from substrate.agents.storage.history import AncestryCheckpointResolver, DefaultHistoryResolver
        from substrate.kernel.exceptions import BranchAlreadyExistsError
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

        # delete_branch removes only the pointer — shared ancestry (node-1,
        # node-2) survives since main still references it.
        await provider.delete_branch(session_id, "experiment")
        assert await provider.get_branch(session_id, "experiment") is None
        assert await provider.get_node("node-2") is not None
        main_branch = await provider.get_branch(session_id, "main")
        assert main_branch is not None and main_branch.head_message_id == "node-3"

        with pytest.raises(ValueError):
            await provider.delete_branch(session_id, "main")
    finally:
        await provider.delete_session(session_id)
        await provider.disconnect()

