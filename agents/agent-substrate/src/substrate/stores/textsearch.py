"""Full-text search, spelled once per database.

SQL has no common syntax for it, so this is the one place the two dialects part. A part with searchable text
(memory, vectors) declares the table and column; this gives it the DDL that makes them searchable, the query
that ranks by relevance, and — for erasure — the step that drops deleted words from the index.

* SQLite: an FTS5 index beside the table (porter stemming), kept in step by triggers; ranked by ``bm25``.
* PostgreSQL: a generated ``tsvector`` column on the table with a GIN index (English stemming); ranked by ``ts_rank``.

Either way a query is only ever *words*: each is quoted (SQLite) or run through ``plainto_tsquery`` (PostgreSQL),
so nothing a caller types can be search syntax.
"""

from __future__ import annotations

import re

_WORDS = re.compile(r"\w+", re.UNICODE)


def words(text: str) -> list[str]:
    return _WORDS.findall(text)


def ddl(dialect: str, *, index: str, table: str, column: str = "text") -> str:
    """What to append to a table's migration so ``column`` can be searched. ``table`` must have a ``seq`` key."""
    if dialect == "postgresql":
        return (
            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS tsv tsvector "
            f"GENERATED ALWAYS AS (to_tsvector('english', {column})) STORED;\n"
            f"CREATE INDEX IF NOT EXISTS {index}_tsv_idx ON {table} USING gin (tsv);\n"
        )
    return f"""
CREATE VIRTUAL TABLE IF NOT EXISTS {index} USING fts5(
    {column}, content='{table}', content_rowid='seq', tokenize='porter unicode61'
);
CREATE TRIGGER IF NOT EXISTS {index}_insert AFTER INSERT ON {table} BEGIN
    INSERT INTO {index} (rowid, {column}) VALUES (new.seq, new.{column});
END;
CREATE TRIGGER IF NOT EXISTS {index}_delete AFTER DELETE ON {table} BEGIN
    INSERT INTO {index} ({index}, rowid, {column}) VALUES ('delete', old.seq, old.{column});
END;
CREATE TRIGGER IF NOT EXISTS {index}_update AFTER UPDATE OF {column} ON {table} BEGIN
    INSERT INTO {index} ({index}, rowid, {column}) VALUES ('delete', old.seq, old.{column});
    INSERT INTO {index} (rowid, {column}) VALUES (new.seq, new.{column});
END;
"""


def ranked(dialect: str, *, index: str, table: str, alias: str, query_words: list[str]) -> tuple[str, str, list[str]]:
    """``(from, where, params)`` for ``SELECT {alias}.*, <score> AS score FROM <from> WHERE <where>``: the rows of
    ``table`` (as ``alias``) that contain every word, with their relevance as ``score`` (higher is better).

    ``from`` and ``where`` precede the caller's own conditions, so ``params`` come first among its parameters.
    """
    if dialect == "postgresql":
        return (
            f"{table} {alias}, plainto_tsquery('english', ?) AS query",
            f"{alias}.tsv @@ query",
            [" ".join(query_words)],
        )
    match = " ".join('"' + word.replace('"', '""') + '"' for word in query_words)
    return f"{index} JOIN {table} {alias} ON {alias}.seq = {index}.rowid", f"{index} MATCH ?", [match]


def score(dialect: str, *, index: str, alias: str) -> str:
    """The relevance expression to select alongside ``ranked``."""
    return f"ts_rank({alias}.tsv, query)" if dialect == "postgresql" else f"-bm25({index})"


def compact(dialect: str, *, index: str) -> str | None:
    """SQL that rewrites the index so deleted words are gone from it, not merely marked deleted (``None``: nothing to do)."""
    return None if dialect == "postgresql" else f"INSERT INTO {index} ({index}) VALUES ('optimize')"
