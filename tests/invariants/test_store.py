"""Invariant register — the store (rows I31–I36).

The engine's state lives in one folder that the library lays out and versions. What has to hold of it: a
transaction that returned survives a hard kill, two processes on one folder never lose each other's writes, a
folder from a newer build is refused rather than misread, and a crash while upgrading is just resumed.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from substrate.runtime import Runtime
from substrate.stores import Store, StoreVersionError, connect, migrate

COUNTER = [
    "CREATE TABLE IF NOT EXISTS counter (name TEXT PRIMARY KEY, n INTEGER NOT NULL);"
]


async def test_connecting_lays_out_the_folder_and_reopening_finds_what_was_written(
    tmp_path: Path,
) -> None:
    """Pointing at a folder is all it takes: the library creates what is inside it, and the same folder opened
    again is the same store."""
    folder = tmp_path / "state"
    store = await connect(folder)
    assert (
        (folder / "substrate.db").is_file()
        and (folder / "files").is_dir()
        and (folder / "index").is_dir()
    )
    await migrate(store.database, "test", COUNTER)
    async with store.database.transaction() as tx:
        await tx.execute("INSERT INTO counter (name, n) VALUES ('x', 7)")
    await store.aclose()

    async with Store.at(folder) as again:
        async with again.database.transaction() as tx:
            row = await tx.fetchone("SELECT n FROM counter WHERE name = 'x'")
    assert row is not None and row["n"] == 7


async def test_a_committed_transaction_survives_the_process_being_killed(
    tmp_path: Path,
) -> None:
    """Durable means a transaction that returned is on disk: the writer is killed without closing anything —
    no checkpoint, no flush — and a fresh process still reads it. A transaction killed half way is not there."""
    script = textwrap.dedent(
        """
        import asyncio, os, sys
        from substrate.stores import connect, migrate

        async def main():
            store = await connect(sys.argv[1])
            await migrate(store.database, "test", ["CREATE TABLE IF NOT EXISTS counter (name TEXT PRIMARY KEY, n INTEGER NOT NULL);"])
            async with store.database.transaction() as tx:
                await tx.execute("INSERT INTO counter (name, n) VALUES ('committed', 1)")
            try:
                async with store.database.transaction() as tx:
                    await tx.execute("INSERT INTO counter (name, n) VALUES ('half', 1)")
                    os._exit(9)
            finally:
                pass

        asyncio.run(main())
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path / "state")],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 9, result.stderr

    async with Store.at(tmp_path / "state") as store:
        async with store.database.transaction() as tx:
            names = {
                row["name"] for row in await tx.fetchall("SELECT name FROM counter")
            }
    assert names == {"committed"}


async def test_two_stores_on_one_folder_never_lose_each_others_writes(
    tmp_path: Path,
) -> None:
    """Workers on one host share the folder. Every increment is a read-then-write in a transaction, from two
    independent connections at once; none may be lost."""
    first, second = Store.at(tmp_path / "state"), Store.at(tmp_path / "state")
    await first.start()
    await second.start()
    await migrate(first.database, "test", COUNTER)
    async with first.database.transaction() as tx:
        await tx.execute("INSERT INTO counter (name, n) VALUES ('hits', 0)")

    async def bump(store: Store) -> None:
        for _ in range(25):
            async with store.database.transaction() as tx:
                row = await tx.fetchone("SELECT n FROM counter WHERE name = 'hits'")
                await tx.execute(
                    "UPDATE counter SET n = ? WHERE name = 'hits'", row["n"] + 1
                )

    await asyncio.gather(*(bump(store) for store in (first, second, first, second)))
    async with first.database.transaction() as tx:
        row = await tx.fetchone("SELECT n FROM counter WHERE name = 'hits'")
    await first.aclose()
    await second.aclose()
    assert row["n"] == 100


async def test_a_folder_written_by_a_newer_build_is_refused(tmp_path: Path) -> None:
    """Reading tables a newer build changed would misread them silently, so a version this build does not
    understand is an error naming both versions."""
    async with Store.at(tmp_path / "state") as store:
        await migrate(store.database, "test", [*COUNTER, "SELECT 1;"])
    async with Store.at(tmp_path / "state") as store:
        with pytest.raises(
            StoreVersionError, match=r"version 2.*only understands up to 1"
        ):
            await migrate(store.database, "test", COUNTER)


async def test_an_upgrade_that_crashed_half_way_is_resumed_not_repeated_wrongly(
    tmp_path: Path,
) -> None:
    """Migrations are applied in order and recorded after they run, so a crash between the two — or two processes
    starting together — runs the script again; scripts are idempotent, and the version is recorded once."""
    async with Store.at(tmp_path / "state") as store:
        await store.database.script(
            COUNTER[0]
        )  # the crash: the script ran, the version was never recorded
        await migrate(store.database, "test", COUNTER)
        await migrate(store.database, "test", COUNTER)
        async with store.database.transaction() as tx:
            rows = await tx.fetchall(
                "SELECT version FROM substrate_migrations WHERE component = 'test'"
            )
    assert [row["version"] for row in rows] == [1]


async def test_the_database_is_opened_for_durability(tmp_path: Path) -> None:
    """Write-ahead log so readers never block the writer, and ``synchronous=FULL``: the engine records an intent
    before it acts and the answer after, and both have to outlive a power cut."""
    async with Store.at(tmp_path / "state") as store:
        async with store.database.transaction() as tx:
            journal = await tx.fetchone("PRAGMA journal_mode")
            synchronous = await tx.fetchone("PRAGMA synchronous")
    assert journal[0] == "wal" and synchronous[0] == 2


async def test_a_runtime_closes_a_store_it_opened_and_leaves_one_it_was_given(
    tmp_path: Path,
) -> None:
    """``Runtime.open(folder)`` owns the store it made; ``Runtime(store)`` shares one, so closing the runtime must
    not pull the store out from under whoever else uses it."""
    async with Runtime.open(tmp_path / "owned") as runtime:
        opened = runtime._source
    with pytest.raises(AssertionError, match="not started"):
        async with opened.database.transaction():
            pass

    async with Store.at(tmp_path / "shared") as store:
        async with Runtime(store):
            pass
        async with store.database.transaction() as tx:
            assert await tx.fetchone("SELECT 1 AS one") is not None


async def test_hostile_thread_and_branch_names_are_just_data_and_touch_no_other_file(
    tmp_path: Path,
) -> None:
    """Names reach the store from request bodies. They are bound parameters, never paths and never part of a
    statement, so a traversal string or an injection attempt is stored and read back like any other name — and the
    folder gains nothing but the database's own files."""
    from substrate.stores import MessageNode
    from substrate.types import ChatMessage, Role, TextBlock

    store = Store.at(tmp_path / "state")
    node = MessageNode(
        session_id="../s'; DROP TABLE thread_nodes; --",
        run_id="r",
        payload=ChatMessage(role=Role.USER, content=[TextBlock(text="hi")]),
    )
    await store.threads.append_and_advance(node, branch_id="../../b")
    branch = await store.threads.get_branch(node.session_id, "../../b")
    await store.aclose()

    assert branch is not None and branch.head_message_id == node.id
    assert {p.name for p in tmp_path.iterdir()} == {"state"}
    assert {p.name for p in (tmp_path / "state").iterdir()} <= {
        "substrate.db",
        "substrate.db-wal",
        "substrate.db-shm",
        "files",
        "index",
    }


async def test_a_turn_killed_between_two_appends_leaves_a_thread_that_is_whole(
    tmp_path: Path,
) -> None:
    """The branch head moves with the node in one transaction. A process killed after appending the first of two
    messages and half way through the second finds, on reopening, a thread ending at the first — never a head
    pointing at a node that is not there, nor a node the head skipped."""
    script = textwrap.dedent(
        """
        import asyncio, os, sys
        from substrate.stores import Store, MessageNode
        from substrate.types import ChatMessage, Role, TextBlock

        def msg(parent, text):
            return MessageNode(session_id="s", parent_id=parent, run_id="r", payload=ChatMessage(role=Role.USER, content=[TextBlock(text=text)]))

        async def main():
            store = Store.at(sys.argv[1])
            first = msg(None, "first")
            await store.threads.append_and_advance(first, "main")
            async with store.database.transaction() as tx:
                second = msg(first.id, "second")
                await tx.execute(
                    "INSERT INTO thread_nodes (id, session_id, parent_id, run_id, payload_json, workspace_snapshot_id, created_at) VALUES (?, ?, ?, ?, ?, NULL, ?)",
                    second.id, "s", first.id, "r", "{}", second.created_at.isoformat(),
                )
                os._exit(7)

        asyncio.run(main())
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path / "state")],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 7, result.stderr

    async with Store.at(tmp_path / "state") as store:
        branch = await store.threads.get_branch("s", "main")
        head = await store.threads.get_node(branch.head_message_id)
        async with store.database.transaction() as tx:
            nodes = await tx.fetchall(
                "SELECT id FROM thread_nodes WHERE session_id = 's'"
            )
    assert head is not None and head.payload.text == "first" and len(nodes) == 1


def test_a_store_nobody_closed_exits_cleanly_and_keeps_what_was_committed(
    tmp_path: Path,
) -> None:
    """Most programs never call ``aclose`` on the store an agent opened for itself. Leaving the process must neither
    print a traceback nor lose anything: the connection is released at exit, after the thread pool is gone."""
    script = textwrap.dedent(
        """
        import asyncio, sys
        from substrate.stores import Store, MessageNode
        from substrate.types import ChatMessage, Role, TextBlock

        async def main():
            store = Store.at(sys.argv[1])
            node = MessageNode(session_id="s", run_id="r", payload=ChatMessage(role=Role.USER, content=[TextBlock(text="kept")]))
            await store.threads.append_and_advance(node, "main")

        asyncio.run(main())
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path / "state")],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0 and result.stderr == "", result.stderr

    async def read() -> str:
        async with Store.at(tmp_path / "state") as store:
            branch = await store.threads.get_branch("s", "main")
            node = await store.threads.get_node(branch.head_message_id)
            return node.payload.text

    assert asyncio.run(read()) == "kept"
