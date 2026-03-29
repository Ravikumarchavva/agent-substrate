"""CapabilitySearchTool — unified discovery for tools and skills.

Replaces ``ToolSearchTool`` with a richer interface that searches both
tools and skills, supports category browsing, and provides the agent
with a structured overview of the capability hierarchy.

The tool exposes three actions:
  - **search** — free-text query across all capabilities (tools + skills)
  - **browse** — list items registered under a category path
  - **list_categories** — show the top-level category tree with descriptions
"""

from __future__ import annotations

from typing import Any, List, Optional

from raavan.core.tools.base_tool import BaseTool, ToolResult, ToolRisk


class CapabilitySearchTool(BaseTool):
    """Meta-tool for discovering tools and skills in the capability catalogue."""

    def __init__(self, catalog: Any) -> None:
        from raavan.core.tools.catalog import CapabilityRegistry

        if not isinstance(catalog, CapabilityRegistry):
            raise TypeError(
                f"Expected CapabilityRegistry, got {type(catalog).__name__}"
            )
        self._catalog: CapabilityRegistry = catalog
        super().__init__(
            name="capability_search",
            description=(
                "Search for tools and skills by capability, browse categories, "
                "or list the full category tree.  Use this to discover what "
                "tools and skills are available before attempting to call them."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["search", "browse", "list_categories"],
                        "description": (
                            "Action to perform: "
                            "'search' = free-text query, "
                            "'browse' = list items in a category, "
                            "'list_categories' = show category tree"
                        ),
                    },
                    "query": {
                        "type": "string",
                        "description": (
                            "Free-text query describing the capability needed "
                            "(required for 'search' action)"
                        ),
                    },
                    "category_path": {
                        "type": "string",
                        "description": (
                            "Category path to browse, e.g. 'data/visualization' "
                            "(required for 'browse' action)"
                        ),
                    },
                    "kind": {
                        "type": "string",
                        "enum": ["tool", "skill"],
                        "description": (
                            "Filter results by kind: 'tool' or 'skill' "
                            "(optional, for 'search' action only)"
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of results to return (default: 8)",
                    },
                },
                "required": ["action"],
                "additionalProperties": False,
            },
            risk=ToolRisk.SAFE,
            category="system",
            tags=[
                "discovery",
                "find",
                "search",
                "help",
                "browse",
                "navigate",
                "tool",
                "skill",
            ],
            aliases=["tool_search", "find_tool", "find_skill", "search_tools"],
        )

    async def execute(  # type: ignore[override]
        self,
        *,
        action: str,
        query: str = "",
        category_path: str = "",
        kind: Optional[str] = None,
        limit: int = 8,
    ) -> ToolResult:
        """Execute the capability search action."""
        limit = max(1, min(limit, 20))

        if action == "search":
            return self._do_search(query, kind=kind, limit=limit)
        if action == "browse":
            return self._do_browse(category_path, limit=limit)
        if action == "list_categories":
            return self._do_list_categories()

        return ToolResult(
            content=[
                {
                    "type": "text",
                    "text": f"Unknown action: {action!r}. Use 'search', 'browse', or 'list_categories'.",
                }
            ],
            is_error=True,
        )

    def _do_search(
        self,
        query: str,
        *,
        kind: Optional[str] = None,
        limit: int = 8,
    ) -> ToolResult:
        if not query.strip():
            return ToolResult(
                content=[
                    {
                        "type": "text",
                        "text": "Please provide a 'query' for the search action.",
                    }
                ],
                is_error=True,
            )

        from typing import Literal

        kind_filter: Optional[Literal["tool", "skill"]] = None
        if kind in ("tool", "skill"):
            kind_filter = kind  # type: ignore[assignment]

        matches = self._catalog.search(
            query,
            limit=limit,
            kind_filter=kind_filter,
            exclude_names={"capability_search"},
        )

        if not matches:
            return ToolResult(
                content=[
                    {
                        "type": "text",
                        "text": f"No matching capabilities found for: {query!r}",
                    }
                ],
                app_data={"matched_tool_names": [], "matched_skill_names": []},
            )

        lines: List[str] = []
        matched_tool_names: List[str] = []
        matched_skill_names: List[str] = []

        for entry in matches:
            kind_label = f"[{entry.kind.upper()}]"
            cat_label = f" ({entry.category})" if entry.category else ""
            line = f"- {entry.name}{cat_label} {kind_label}: {entry.description}"

            # Add parameter info for tools
            if entry.tool is not None:
                props = entry.tool.input_schema.get("properties", {})
                if props:
                    param_names = ", ".join(props.keys())
                    line += f" | parameters: {param_names}"

            lines.append(line)

            if entry.kind == "tool":
                matched_tool_names.append(entry.name)
            else:
                matched_skill_names.append(entry.name)

        text = f"Found {len(matches)} capabilities for '{query}':\n" + "\n".join(lines)

        return ToolResult(
            content=[{"type": "text", "text": text}],
            app_data={
                "matched_tool_names": matched_tool_names,
                "matched_skill_names": matched_skill_names,
            },
        )

    def _do_browse(self, category_path: str, *, limit: int = 8) -> ToolResult:
        if not category_path.strip():
            return ToolResult(
                content=[
                    {
                        "type": "text",
                        "text": "Please provide a 'category_path' for the browse action.",
                    }
                ],
                is_error=True,
            )

        cat_node = self._catalog.get_category(category_path)
        if cat_node is None:
            # Suggest valid categories
            all_cats = [c.path for c in self._catalog.list_categories()]
            return ToolResult(
                content=[
                    {
                        "type": "text",
                        "text": f"Category '{category_path}' not found. Top-level categories: {', '.join(all_cats)}",
                    }
                ],
                is_error=True,
            )

        entries = self._catalog.browse(category_path)
        subcats = self._catalog.list_categories(category_path)

        lines: List[str] = [f"Category: {cat_node.path} — {cat_node.description}"]

        if subcats:
            lines.append("\nSubcategories:")
            for sc in subcats:
                count = len(self._catalog.browse(sc.path))
                lines.append(f"  - {sc.path}: {sc.description} ({count} items)")

        matched_tool_names: List[str] = []
        matched_skill_names: List[str] = []

        if entries:
            lines.append(f"\nItems ({len(entries)}):")
            for entry in entries[:limit]:
                kind_label = f"[{entry.kind.upper()}]"
                lines.append(f"  - {entry.name} {kind_label}: {entry.description}")
                if entry.kind == "tool":
                    matched_tool_names.append(entry.name)
                else:
                    matched_skill_names.append(entry.name)

        return ToolResult(
            content=[{"type": "text", "text": "\n".join(lines)}],
            app_data={
                "matched_tool_names": matched_tool_names,
                "matched_skill_names": matched_skill_names,
            },
        )

    def _do_list_categories(self) -> ToolResult:
        top_cats = self._catalog.list_categories()
        lines: List[str] = ["Available capability categories:\n"]

        for cat in top_cats:
            item_count = len(self._catalog.browse(cat.path))
            subcats = self._catalog.list_categories(cat.path)
            lines.append(f"- {cat.path}: {cat.description} ({item_count} items)")
            for sc in subcats:
                sc_count = len(self._catalog.browse(sc.path))
                lines.append(f"    - {sc.path}: {sc.description} ({sc_count} items)")

        lines.append(
            "\nUse action='browse' with category_path to explore a category, "
            "or action='search' with a query to find specific capabilities."
        )

        return ToolResult(
            content=[{"type": "text", "text": "\n".join(lines)}],
        )
