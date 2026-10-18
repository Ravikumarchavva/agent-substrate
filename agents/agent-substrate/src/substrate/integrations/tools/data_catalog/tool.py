"""DataCatalogTool — find and understand governed warehouse tables before writing code against them.

Read-only and scoped: every call uses a short-lived token minted for the run's own user and tenant, so the
catalog shows exactly the tables and views that user may read and nothing else. It returns names,
columns and a few sample rows; the analysis itself happens in the code interpreter with
``substrate_data_client``, where results never pass through the model.
"""

from __future__ import annotations

import re
from typing import Any, Protocol, runtime_checkable

from substrate.tools import ToolExecutionResult, ToolRisk
from substrate.types import RunScope, TextBlock, scope_of

TABLE_NAME = re.compile(
    r"^[a-z_][a-z0-9_]{0,63}\.[a-z_][a-z0-9_]{0,63}\.[a-z_][a-z0-9_]{0,63}$"
)
SAMPLE_ROWS = 5
MAX_CELL = 60


@runtime_checkable
class DataTokens(Protocol):
    """Where the tool gets the data service's address and a token for one run's user."""

    service_url: str

    async def token_for(
        self, scope: RunScope, ttl_seconds: int = 120
    ) -> str | None: ...


def _cell(value: Any) -> str:
    text = "NULL" if value is None else str(value)
    return text if len(text) <= MAX_CELL else text[: MAX_CELL - 1] + "…"


def _table(columns: list[str], rows: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(columns) + " |", "|" + "---|" * len(columns)]
    lines += ["| " + " | ".join(_cell(v) for v in row) + " |" for row in rows]
    return "\n".join(lines)


class DataCatalogTool:
    name = "data_catalog"
    risk = ToolRisk.SAFE
    idempotent = True
    concurrency_safe = True
    description = (
        "Find and inspect the warehouse tables and views you may read. "
        "action='search' takes a word and lists matching tables and views with their columns; "
        "action='describe' takes a full name (catalog.schema.table) and returns its columns, size, "
        "partitioning and a few sample rows. Use it to learn what data exists, then analyse it with "
        "the code interpreter: `import substrate_data_client as sd; sd.sql('SELECT ...')` returns a "
        "Polars DataFrame (do filtering and aggregation in SQL, not in Python)."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["search", "describe"]},
            "query": {
                "type": "string",
                "description": "For search: a word in a table's name, comment or a column. Empty lists recent tables.",
            },
            "table": {
                "type": "string",
                "description": "For describe: the full name, catalog.schema.table_or_view.",
            },
        },
        "required": ["action"],
        "additionalProperties": False,
    }

    def __init__(
        self, tokens: DataTokens, *, transport: Any | None = None, timeout_s: float = 30
    ):
        self._tokens, self._transport, self._timeout = tokens, transport, timeout_s

    async def execute(
        self,
        *,
        ctx: Any = None,
        action: str = "search",
        query: str = "",
        table: str = "",
        **_: Any,
    ) -> ToolExecutionResult:
        scope = scope_of(ctx)
        token = await self._tokens.token_for(scope)
        if token is None:
            return self._error(
                "Data access needs a signed-in, tenant-scoped conversation."
            )
        import httpx

        async with httpx.AsyncClient(
            base_url=self._tokens.service_url + "/api/v1",
            headers={"Authorization": f"Bearer {token}"},
            timeout=self._timeout,
            transport=self._transport,
        ) as client:
            try:
                if action == "search":
                    return await self._search(client, query.strip()[:200])
                if action == "describe":
                    return await self._describe(client, table.strip())
            except httpx.HTTPError:
                return self._error("The data service is unreachable right now.")
        return self._error("action must be 'search' or 'describe'.")

    # ------------------------------------------------------------------ actions
    async def _search(self, client: Any, query: str) -> ToolExecutionResult:
        response = await client.get("/search", params={"q": query, "limit": 20})
        if response.status_code != 200:
            return self._error(self._problem(response))
        results = response.json()["results"]
        if not results:
            return self._ok(
                f"Nothing matches {query!r}. Try a different word, or search with no query to list recent data."
            )
        lines = [f"{len(results)} match(es):"]
        for item in results:
            size = f", {item['row_count']:,} rows" if "row_count" in item else ""
            columns = ", ".join(item["columns"][:12]) + (
                " …" if len(item["columns"]) > 12 else ""
            )
            note = f" — {item['comment']}" if item.get("comment") else ""
            lines.append(
                f"- {item['full_name']} ({item['kind']}{size}){note}\n  columns: {columns}"
            )
        return self._ok("\n".join(lines))

    async def _describe(self, client: Any, name: str) -> ToolExecutionResult:
        if not TABLE_NAME.fullmatch(name):
            return self._error(
                "table must look like catalog.schema.table (lower-case letters, digits, _)."
            )
        catalog, schema, short = name.split(".")
        base = f"/catalogs/{catalog}/schemas/{schema}"
        kind, response = "table", await client.get(f"{base}/tables/{short}")
        if response.status_code == 404:
            kind, response = "view", await client.get(f"{base}/views/{short}")
        if response.status_code != 200:
            return self._error(self._problem(response))
        info = response.json()
        lines = [f"{name} ({kind})"]
        if info.get("comment"):
            lines.append(info["comment"])
        if kind == "table":
            lines.append(
                f"{info['row_count']:,} rows in {info['file_count']} files; partitioned by "
                + (
                    ", ".join(
                        f"{p['transform']}({p['column']})" for p in info["partitioning"]
                    )
                    or "nothing"
                )
                + ". Filter on the partition column to read less."
            )
        lines.append(
            "Columns:\n"
            + "\n".join(f"- {c['name']}: {c['type']}" for c in info["columns"])
        )
        sample = await client.post(
            "/statements",
            json={
                "sql": f"SELECT * FROM {name} LIMIT {SAMPLE_ROWS}",
                "max_rows": SAMPLE_ROWS,
            },
        )
        if sample.status_code == 200:
            body = sample.json()
            lines.append(
                f"Sample ({len(body['rows'])} rows):\n"
                + _table([c["name"] for c in body["columns"]], body["rows"])
            )
        else:
            lines.append(f"(No sample: {self._problem(sample)})")
        return self._ok("\n".join(lines))

    # ------------------------------------------------------------------ results
    @staticmethod
    def _problem(response: Any) -> str:
        try:
            detail = response.json().get("detail")
        except ValueError:
            detail = None
        return f"{response.status_code}: {detail or 'request failed'}"

    @staticmethod
    def _ok(text: str) -> ToolExecutionResult:
        return ToolExecutionResult(content=[TextBlock(text=text)])

    @staticmethod
    def _error(message: str) -> ToolExecutionResult:
        return ToolExecutionResult(content=[TextBlock(text=message)], is_error=True)


__all__ = ["DataCatalogTool", "DataTokens"]
