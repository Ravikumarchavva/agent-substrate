"""Citations for what the ``documents`` tool returns: numbered, stable across a conversation, built only from what the catalog holds.

A model cannot cite a source it was never given a number for, and the numbers do not change under it: ``[2]`` means the same section
for the whole conversation, whichever tool call returned it. The wire shape (``Citation.to_wire``) is the one the chat UI already renders.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Citation:
    index: int
    file_name: str
    file_id: str = ""
    session_path: str = ""
    thread_id: str = ""
    page: int | None = None
    pages: tuple[int, ...] = ()
    score: float = 0.0
    snippet: str = ""
    backend: str = "library"
    preceding_context: str = ""
    following_context: str = ""

    def label(self) -> str:
        """``(report.pdf, p.5)`` — appended to a passage so the model can say where it read it without inventing the page."""
        if not self.pages:
            return f"({self.file_name})"
        first, last = min(self.pages), max(self.pages)
        return f"({self.file_name}, {'p.' + str(first) if first == last else f'pp.{first}-{last}'})"

    def to_wire(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "file_name": self.file_name,
            "file_id": self.file_id,
            "session_path": self.session_path,
            "thread_id": self.thread_id,
            "page": self.page,
            "pages": list(self.pages),
            "score": round(self.score, 4),
            "snippet": self.snippet,
            "backend": self.backend,
            "preceding_context": self.preceding_context,
            "following_context": self.following_context,
        }


class CitationLedger:
    """``(collection, document, section) → index``, one per tool instance; numbering starts at 1 and never changes."""

    def __init__(self, *, max_entries: int = 2000) -> None:
        self._indices: dict[tuple[str, str, int], int] = {}
        self._max = max_entries

    def index_for(self, collection: str, document: str, section: int) -> int:
        key = (collection, document, section)
        found = self._indices.get(key)
        if found is not None:
            return found
        if (
            len(self._indices) >= self._max
        ):  # a long-lived tool must not grow without bound; renumbering later turns is harmless
            self._indices.clear()
        self._indices[key] = len(self._indices) + 1
        return self._indices[key]


__all__ = ["Citation", "CitationLedger"]
