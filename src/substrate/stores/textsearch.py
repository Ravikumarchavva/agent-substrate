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


_STOPWORDS = frozenset(
    "a an and are as at be but by for from has have how i if in into is it its of on or that the their then there these this to was were what when where which who why will with you your".split()
)
MAX_TERMS = 16


def terms(query: str) -> list[str]:
    """The words of ``query`` worth searching for: stopwords dropped (unless that leaves nothing), duplicates folded, at most ``MAX_TERMS``."""
    found = list(dict.fromkeys(word for word in words(query) if word))
    kept = [word for word in found if word.lower() not in _STOPWORDS] or found
    return kept[:MAX_TERMS]


def snippet(text: str, query_words: list[str], *, width: int = 240) -> str:
    """About ``width`` characters of ``text`` around the first place any of the words occurs (the start if none does)."""
    flat = " ".join(text.split())
    lower = flat.lower()
    at = min((i for i in (lower.find(w.lower()) for w in query_words) if i >= 0), default=-1)
    if at < 0 or len(flat) <= width:
        return flat[:width] + ("…" if len(flat) > width else "")
    start = max(0, at - width // 3)
    end = min(len(flat), start + width)
    return ("…" if start else "") + flat[start:end] + ("…" if end < len(flat) else "")


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


def ranked(
    dialect: str, *, index: str, table: str, alias: str, query_words: list[str], match: str = "all"
) -> tuple[str, str, list[str]]:
    """``(from, where, params)`` for ``SELECT {alias}.*, <score> AS score FROM <from> WHERE <where>``: the rows of
    ``table`` (as ``alias``) that contain every word (``match="all"``) or any of them (``match="any"``), with their
    relevance as ``score`` (higher is better).

    ``from`` and ``where`` precede the caller's own conditions, so ``params`` come first among its parameters.
    """
    if match not in ("all", "any"):
        raise ValueError(f"match must be 'all' or 'any', got {match!r}")
    if dialect == "postgresql":
        if match == "any":  # the words are \w+ only (see ``words``), so none of them can be query syntax
            return (
                f"{table} {alias}, to_tsquery('english', ?) AS query",
                f"{alias}.tsv @@ query",
                [" | ".join(query_words)],
            )
        return (
            f"{table} {alias}, plainto_tsquery('english', ?) AS query",
            f"{alias}.tsv @@ query",
            [" ".join(query_words)],
        )
    joiner = " OR " if match == "any" else " "
    quoted = joiner.join('"' + word.replace('"', '""') + '"' for word in query_words)
    return f"{index} JOIN {table} {alias} ON {alias}.seq = {index}.rowid", f"{index} MATCH ?", [quoted]


def score(dialect: str, *, index: str, alias: str) -> str:
    """The relevance expression to select alongside ``ranked``."""
    return f"ts_rank({alias}.tsv, query)" if dialect == "postgresql" else f"-bm25({index})"


def compact(dialect: str, *, index: str) -> str | None:
    """SQL that rewrites the index so deleted words are gone from it, not merely marked deleted (``None``: nothing to do)."""
    return None if dialect == "postgresql" else f"INSERT INTO {index} ({index}) VALUES ('optimize')"
