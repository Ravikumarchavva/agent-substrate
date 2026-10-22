import pytest

from loamz.engine.sql import SchemaVersionError, migrate, under

SCRIPTS = [
    "CREATE TABLE IF NOT EXISTS notes (id {pk}, owner TEXT NOT NULL, body TEXT NOT NULL)",
    "CREATE UNIQUE INDEX IF NOT EXISTS notes_owner_body ON notes (owner, body)",
]


async def test_migrations_apply_once_and_refuse_a_newer_database(db):
    await migrate(db, "notes", SCRIPTS)
    await migrate(db, "notes", SCRIPTS)  # idempotent
    with pytest.raises(SchemaVersionError):
        await migrate(db, "notes", SCRIPTS[:1])


async def test_a_transaction_commits_or_leaves_nothing(db):
    await migrate(db, "notes", SCRIPTS)
    async with db.transaction() as tx:
        await tx.execute("INSERT INTO notes (owner, body) VALUES (?, ?)", "a", "kept")
    with pytest.raises(RuntimeError):
        async with db.transaction() as tx:
            await tx.execute("INSERT INTO notes (owner, body) VALUES (?, ?)", "a", "lost")
            raise RuntimeError("boom")
    async with db.transaction() as tx:
        rows = await tx.fetchall("SELECT body FROM notes ORDER BY body")
    assert [row["body"] for row in rows] == ["kept"]


async def test_unique_violations_are_recognised(db):
    await migrate(db, "notes", SCRIPTS)
    async with db.transaction() as tx:
        await tx.execute("INSERT INTO notes (owner, body) VALUES (?, ?)", "a", "x")
    with pytest.raises(Exception) as caught:
        async with db.transaction() as tx:
            await tx.execute("INSERT INTO notes (owner, body) VALUES (?, ?)", "a", "x")
    assert db.is_unique_violation(caught.value)


async def test_under_matches_a_name_and_what_lies_below_it_only(db):
    await migrate(db, "notes", SCRIPTS)
    async with db.transaction() as tx:
        for owner in ("t_1", "t_1/u", "t_10", "t%1"):
            await tx.execute("INSERT INTO notes (owner, body) VALUES (?, ?)", owner, owner)
        clause, params = under("owner", "t_1")
        rows = await tx.fetchall(f"SELECT owner FROM notes WHERE {clause} ORDER BY owner", *params)
    assert [row["owner"] for row in rows] == ["t_1", "t_1/u"]
