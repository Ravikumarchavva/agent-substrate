"""CapabilityRegistry — unified hierarchical registry for tools and skills.

Replaces ``ToolRegistry`` with a two-level category tree that organises
both ``BaseTool`` instances and ``SkillMetadata`` entries under a shared
taxonomy.  A multi-signal lexical scorer powers free-text search across
names, aliases, tags, categories, and descriptions.

Key design decision — **search is always global**.  Categories boost scores
but never filter, so a miscategorised item is still discoverable via its
tags, aliases, or description tokens.

Usage::

    from raavan.core.tools.catalog import CapabilityRegistry

    catalog = CapabilityRegistry()
    catalog.register_tool(CalculatorTool(), category="productivity",
                          tags=["math", "calculate"], aliases=["math_tool"])
    catalog.register_skill(meta, category="research",
                           tags=["web", "search"], aliases=["internet-research"])

    results = catalog.search("make a chart")   # returns CapabilityEntry list
    items   = catalog.browse("data/visualization")
    cats    = catalog.list_categories()
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Iterator, List, Literal, Optional

from raavan.core.tools.base_tool import BaseTool, ToolRisk

logger = logging.getLogger("raavan.tools.catalog")

# ---------------------------------------------------------------------------
# Stopwords — filtered from query tokenisation
# ---------------------------------------------------------------------------

_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "at",
    "can",
    "do",
    "for",
    "from",
    "has",
    "have",
    "help",
    "how",
    "i",
    "in",
    "is",
    "it",
    "me",
    "my",
    "need",
    "of",
    "on",
    "or",
    "please",
    "show",
    "some",
    "the",
    "to",
    "use",
    "want",
    "what",
    "with",
    "would",
    "you",
}


# ---------------------------------------------------------------------------
# Category node
# ---------------------------------------------------------------------------


@dataclass
class CategoryNode:
    """A node in the two-level category tree."""

    name: str
    path: str  # e.g. "data/visualization"
    description: str
    parent_path: Optional[str] = None
    children: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Capability entry — one item in the catalogue
# ---------------------------------------------------------------------------


@dataclass
class CapabilityEntry:
    """A single tool or skill registered in the catalogue."""

    name: str
    description: str
    kind: Literal["tool", "skill"]
    category: str  # category path, e.g. "data/visualization"
    tags: List[str] = field(default_factory=list)
    aliases: List[str] = field(default_factory=list)
    tool: Optional[BaseTool] = field(default=None, repr=False)
    skill_metadata: Optional[Any] = field(default=None, repr=False)


# ---------------------------------------------------------------------------
# Default category tree (pre-registered)
# ---------------------------------------------------------------------------

_DEFAULT_CATEGORIES: List[CategoryNode] = [
    CategoryNode(
        "system", "system", "Meta-tools for discovery and agent self-management"
    ),
    CategoryNode(
        "communication", "communication", "Human interaction, messaging, notifications"
    ),
    CategoryNode(
        "data", "data", "Data processing, files, storage, and knowledge retrieval"
    ),
    CategoryNode(
        "visualization",
        "data/visualization",
        "Charts, graphs, color tools, and document rendering",
        parent_path="data",
    ),
    CategoryNode(
        "exploration",
        "data/exploration",
        "JSON and document inspection and parsing",
        parent_path="data",
    ),
    CategoryNode(
        "management",
        "data/management",
        "File I/O, storage, and knowledge retrieval",
        parent_path="data",
    ),
    CategoryNode(
        "development",
        "development",
        "Code execution, project management, and developer tools",
    ),
    CategoryNode(
        "execution",
        "development/execution",
        "Code running, API calls, and scripting",
        parent_path="development",
    ),
    CategoryNode(
        "project",
        "development/project",
        "Task management, Kanban boards, and project planning",
        parent_path="development",
    ),
    CategoryNode(
        "research",
        "research",
        "Web browsing, information gathering, analysis, and synthesis",
    ),
    CategoryNode(
        "creative", "creative", "Image generation, writing, and content creation"
    ),
    CategoryNode("media", "media", "Music playback, audio processing, and streaming"),
    CategoryNode(
        "productivity", "productivity", "Utilities, math, time, notes, and planning"
    ),
]


# ---------------------------------------------------------------------------
# CapabilityRegistry
# ---------------------------------------------------------------------------


class CapabilityRegistry:
    """Unified, hierarchical registry for tools and skills.

    The catalog replaces ``ToolRegistry`` with a richer data model:
    - Two-level category tree (``CategoryNode``)
    - Per-entry tags and aliases for cross-cutting discoverability
    - Multi-signal lexical search that boosts on category match
    - Inverted tag index for O(1) tag lookups
    - Alias index for name resolution (``get("music_player")`` → ``spotify_player``)

    Lifecycle
    ---------
    Tools that declare ``async startup()`` / ``async shutdown()`` will be
    called during ``await catalog.startup()`` / ``await catalog.shutdown()``.
    """

    def __init__(self) -> None:
        self._entries: Dict[str, CapabilityEntry] = {}
        self._categories: Dict[str, CategoryNode] = {}
        self._tag_index: Dict[str, set[str]] = {}
        self._alias_index: Dict[str, str] = {}
        # Insertion order list for deterministic iteration
        self._insertion_order: List[str] = []

        # Seed default categories
        for cat in _DEFAULT_CATEGORIES:
            self._categories[cat.path] = cat
        # Wire parent → children
        for cat in _DEFAULT_CATEGORIES:
            if cat.parent_path and cat.parent_path in self._categories:
                parent = self._categories[cat.parent_path]
                if cat.path not in parent.children:
                    parent.children.append(cat.path)

    # ── Registration ─────────────────────────────────────────────────────────

    def register_tool(
        self,
        tool: BaseTool,
        *,
        category: Optional[str] = None,
        tags: Optional[List[str]] = None,
        aliases: Optional[List[str]] = None,
    ) -> "CapabilityRegistry":
        """Register a tool in the catalogue.

        Falls back to tool-level metadata (``tool.category``, ``tool.tags``,
        ``tool.aliases``) when registration-level values are not provided.
        """
        cat = category or getattr(tool, "category", None) or ""
        tag_list = tags or getattr(tool, "tags", None) or []
        alias_list = aliases or getattr(tool, "aliases", None) or []

        entry = CapabilityEntry(
            name=tool.name,
            description=tool.description,
            kind="tool",
            category=cat,
            tags=[t.lower() for t in tag_list],
            aliases=[a.lower() for a in alias_list],
            tool=tool,
        )
        self._put(entry)
        return self

    def register_skill(
        self,
        skill_metadata: Any,
        *,
        category: Optional[str] = None,
        tags: Optional[List[str]] = None,
        aliases: Optional[List[str]] = None,
    ) -> "CapabilityRegistry":
        """Register a skill (by its ``SkillMetadata``) in the catalogue."""
        cat = category or getattr(skill_metadata, "category", None) or ""
        tag_list = tags or getattr(skill_metadata, "tags", None) or []
        alias_list = aliases or getattr(skill_metadata, "aliases", None) or []

        entry = CapabilityEntry(
            name=skill_metadata.name,
            description=skill_metadata.description,
            kind="skill",
            category=cat,
            tags=[t.lower() for t in tag_list],
            aliases=[a.lower() for a in alias_list],
            skill_metadata=skill_metadata,
        )
        self._put(entry)
        return self

    def _put(self, entry: CapabilityEntry) -> None:
        """Internal: insert or replace an entry, updating indexes."""
        old = self._entries.get(entry.name)
        if old is not None:
            # Remove old index entries
            self._remove_indexes(old)
            logger.debug("CapabilityRegistry: replacing existing entry %r", entry.name)
        else:
            self._insertion_order.append(entry.name)

        self._entries[entry.name] = entry

        # Build tag index
        for tag in entry.tags:
            self._tag_index.setdefault(tag, set()).add(entry.name)

        # Build alias index
        for alias in entry.aliases:
            alias_lower = alias.lower()
            self._alias_index[alias_lower] = entry.name

    def _remove_indexes(self, entry: CapabilityEntry) -> None:
        """Remove an entry's tags and aliases from inverted indexes."""
        for tag in entry.tags:
            s = self._tag_index.get(tag)
            if s:
                s.discard(entry.name)
                if not s:
                    del self._tag_index[tag]
        for alias in entry.aliases:
            self._alias_index.pop(alias.lower(), None)

    def unregister(self, name: str) -> None:
        """Remove an entry by name.  No-op if not present."""
        entry = self._entries.pop(name, None)
        if entry is not None:
            self._remove_indexes(entry)
            try:
                self._insertion_order.remove(name)
            except ValueError:
                pass

    # ── Queries ──────────────────────────────────────────────────────────────

    def get(self, name: str) -> Optional[CapabilityEntry]:
        """Return entry by canonical name or alias.  ``None`` if not found."""
        entry = self._entries.get(name)
        if entry is not None:
            return entry
        # Try alias
        canonical = self._alias_index.get(name.lower())
        if canonical is not None:
            return self._entries.get(canonical)
        return None

    def get_tool(self, name: str) -> Optional[BaseTool]:
        """Return the ``BaseTool`` instance by name or alias.  ``None`` if not found or not a tool."""
        entry = self.get(name)
        if entry is not None and entry.tool is not None:
            return entry.tool
        return None

    def all(self) -> List[CapabilityEntry]:
        """Return all entries in insertion order."""
        return [self._entries[n] for n in self._insertion_order if n in self._entries]

    def all_tools(self) -> List[BaseTool]:
        """Return all registered tools in insertion order."""
        result: List[BaseTool] = []
        for n in self._insertion_order:
            entry = self._entries.get(n)
            if entry is not None and entry.tool is not None:
                result.append(entry.tool)
        return result

    def all_skills(self) -> List[Any]:
        """Return all registered skill metadata objects in insertion order."""
        return [
            self._entries[n].skill_metadata
            for n in self._insertion_order
            if n in self._entries and self._entries[n].skill_metadata is not None
        ]

    def by_risk(self, risk: ToolRisk) -> List[CapabilityEntry]:
        """Return tool entries matching the given risk tier."""
        return [
            e
            for e in self._entries.values()
            if e.tool is not None and getattr(e.tool, "risk", None) == risk
        ]

    def names(self) -> List[str]:
        """Return all registered entry names in insertion order."""
        return list(self._insertion_order)

    def browse(self, category_path: str) -> List[CapabilityEntry]:
        """Return all entries within a category (exact match on path)."""
        return [e for e in self._entries.values() if e.category == category_path]

    def list_categories(self, parent: Optional[str] = None) -> List[CategoryNode]:
        """Return categories, optionally filtered to children of *parent*.

        If *parent* is ``None``, returns top-level categories (no parent).
        """
        if parent is None:
            return [c for c in self._categories.values() if c.parent_path is None]
        node = self._categories.get(parent)
        if node is None:
            return []
        return [self._categories[p] for p in node.children if p in self._categories]

    def get_category(self, path: str) -> Optional[CategoryNode]:
        """Return a category node by path."""
        return self._categories.get(path)

    # ── Search ───────────────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        kind_filter: Optional[Literal["tool", "skill"]] = None,
        exclude_names: Optional[set[str]] = None,
    ) -> List[CapabilityEntry]:
        """Return the best matching entries for a free-text query.

        Uses a multi-signal lexical scorer.  Categories BOOST scores but
        never filter — a miscategorised item is always discoverable via
        tags, aliases, or description.
        """
        normalized = query.strip().lower()
        if not normalized:
            return []

        excludes = exclude_names or set()
        ranked: List[tuple[int, CapabilityEntry]] = []

        for entry in self._entries.values():
            if entry.name in excludes:
                continue
            if kind_filter and entry.kind != kind_filter:
                continue
            score = self._score_capability(entry, normalized)
            if score > 0:
                ranked.append((score, entry))

        ranked.sort(key=lambda item: (-item[0], item[1].name))
        return [entry for _, entry in ranked[: max(1, limit)]]

    def _score_capability(self, entry: CapabilityEntry, normalized_query: str) -> int:
        """Score an entry against a normalised (lowered, stripped) query."""
        tokens = self._tokenize(normalized_query)
        score = 0

        # ── Signal 1: Name match ────────────────────────────────────
        if entry.name == normalized_query:
            score += 150
        elif normalized_query in entry.name:
            score += 70

        # ── Signal 2: Alias match ───────────────────────────────────
        for alias in entry.aliases:
            if alias == normalized_query:
                score += 140
                break
            if normalized_query in alias:
                score += 65
                break

        # ── Signal 3: Tag match ─────────────────────────────────────
        for tag in entry.tags:
            if tag == normalized_query:
                score += 50
            elif normalized_query in tag:
                score += 25
        for token in tokens:
            for tag in entry.tags:
                if token == tag:
                    score += 30
                elif token in tag or tag in token:
                    score += 15

        # ── Signal 4: Category boost ────────────────────────────────
        cat_parts = entry.category.lower().split("/") if entry.category else []
        cat_node = self._categories.get(entry.category)
        cat_desc = cat_node.description.lower() if cat_node else ""
        for token in tokens:
            if any(token == part or token in part for part in cat_parts):
                score += 40
                break
            if token in cat_desc:
                score += 20
                break

        # ── Signal 5: Description substring ─────────────────────────
        desc_lower = entry.description.lower()
        if normalized_query in desc_lower:
            score += 30
        for token in tokens:
            if token in desc_lower:
                score += 6

        # ── Signal 6: Schema property names (tools only) ────────────
        if entry.tool is not None:
            haystack = self._tool_search_text(entry.tool)
            for token in tokens:
                if token in haystack:
                    score += 4

        return score

    @staticmethod
    def _tokenize(text: str) -> List[str]:
        """Split text into lowercase tokens, filtering stopwords and short tokens."""
        return [
            token
            for token in re.split(r"[^a-z0-9_]+", text.lower())
            if token and len(token) >= 3 and token not in _STOPWORDS
        ]

    @staticmethod
    def _tool_search_text(tool: BaseTool) -> str:
        """Build a searchable haystack from a tool's schema properties."""
        properties = tool.input_schema.get("properties", {})
        prop_bits: List[str] = []
        for name, prop in properties.items():
            if isinstance(prop, dict):
                prop_bits.append(name)
                description = prop.get("description")
                if isinstance(description, str):
                    prop_bits.append(description)
        return " ".join([tool.name, tool.description, *prop_bits]).lower()

    # ── Dunder protocols ─────────────────────────────────────────────────────

    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self) -> Iterator[CapabilityEntry]:
        """Iterate entries in insertion order."""
        for name in self._insertion_order:
            entry = self._entries.get(name)
            if entry is not None:
                yield entry

    def __contains__(self, name: str) -> bool:
        return name in self._entries or name.lower() in self._alias_index

    def __repr__(self) -> str:
        names = ", ".join(self._insertion_order) or "(empty)"
        return f"<CapabilityRegistry [{names}]>"

    # ── Lifecycle ────────────────────────────────────────────────────────────

    async def startup(self) -> None:
        """Call ``startup()`` on every tool that defines it."""
        for entry in self._entries.values():
            if entry.tool is not None:
                fn: Any = getattr(entry.tool, "startup", None)
                if callable(fn):
                    try:
                        await fn()  # type: ignore[misc]
                    except Exception:
                        logger.exception(
                            "CapabilityRegistry: startup failed for %r", entry.name
                        )

    async def shutdown(self) -> None:
        """Call ``shutdown()`` on every tool that defines it."""
        for entry in self._entries.values():
            if entry.tool is not None:
                fn: Any = getattr(entry.tool, "shutdown", None)
                if callable(fn):
                    try:
                        await fn()  # type: ignore[misc]
                    except Exception:
                        logger.exception(
                            "CapabilityRegistry: shutdown failed for %r", entry.name
                        )

    # ── Factory ──────────────────────────────────────────────────────────────

    @classmethod
    def from_tools_and_skills(
        cls,
        tools: Iterable[BaseTool],
        skills: Optional[Iterable[Any]] = None,
    ) -> "CapabilityRegistry":
        """Build a catalogue from existing tool/skill lists.

        Uses tool-level metadata (``tool.category``, ``tool.tags``,
        ``tool.aliases``) when available.
        """
        catalog = cls()
        for tool in tools:
            catalog.register_tool(tool)
        if skills:
            for skill_meta in skills:
                catalog.register_skill(skill_meta)
        return catalog
