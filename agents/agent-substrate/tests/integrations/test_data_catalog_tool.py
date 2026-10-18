"""DataCatalogTool — search and describe governed tables with a token for the run's own user."""

from __future__ import annotations

import json

import httpx

from substrate.integrations.tools.data_catalog import DataCatalogTool
from substrate.types import RunScope

SCOPE = RunScope(tenant_id="acme", user_id="user-1", thread_id="t1", agent_id="primary")


class _Ctx:
    scope = SCOPE


class _Tokens:
    service_url = "http://data.test"

    def __init__(self, token: str | None = "tok-user-1") -> None:
        self.token, self.asked = token, []

    async def token_for(self, scope, ttl_seconds: int = 120):
        self.asked.append(scope)
        return self.token


def tool(handler, tokens: _Tokens | None = None) -> tuple[DataCatalogTool, _Tokens]:
    tokens = tokens or _Tokens()
    return DataCatalogTool(tokens, transport=httpx.MockTransport(handler)), tokens


def text(result) -> str:
    return result.content[0].text


async def test_search_lists_what_the_users_token_can_see():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "full_name": "main.nyc.yellow",
                        "kind": "table",
                        "row_count": 88039239,
                        "columns": ["VendorID", "fare_amount"],
                        "comment": "Taxi trips",
                    },
                    {
                        "full_name": "main.nyc.monthly_revenue",
                        "kind": "view",
                        "columns": ["month"],
                    },
                ]
            },
        )

    catalog, tokens = tool(handler)
    result = await catalog.execute(ctx=_Ctx(), action="search", query="taxi")

    out = text(result)
    assert "main.nyc.yellow (table, 88,039,239 rows) — Taxi trips" in out
    assert (
        "main.nyc.monthly_revenue (view)" in out
        and "columns: VendorID, fare_amount" in out
    )
    assert seen[0].headers["authorization"] == "Bearer tok-user-1"
    assert seen[0].url.params["q"] == "taxi"
    assert tokens.asked[0].user_id == "user-1" and tokens.asked[0].tenant_id == "acme"


async def test_describe_returns_columns_partitioning_and_a_sample():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/tables/yellow"):
            return httpx.Response(
                200,
                json={
                    "row_count": 1200,
                    "file_count": 4,
                    "comment": None,
                    "partitioning": [{"column": "pickup", "transform": "month"}],
                    "columns": [
                        {"name": "pickup", "type": "TIMESTAMP"},
                        {"name": "fare", "type": "DOUBLE"},
                    ],
                },
            )
        body = json.loads(request.content)
        assert (
            body["sql"] == "SELECT * FROM main.nyc.yellow LIMIT 5"
            and body["max_rows"] == 5
        )
        return httpx.Response(
            200,
            json={
                "columns": [{"name": "pickup"}, {"name": "fare"}],
                "rows": [["2026-08-01", 12.5], ["2026-08-02", None]],
            },
        )

    catalog, _ = tool(handler)
    out = text(
        await catalog.execute(ctx=_Ctx(), action="describe", table="main.nyc.yellow")
    )

    assert "1,200 rows in 4 files; partitioned by month(pickup)" in out
    assert "- fare: DOUBLE" in out and "| 2026-08-02 | NULL |" in out


async def test_describe_falls_back_to_a_view_and_reports_what_the_service_says():
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/tables/secret"):
            return httpx.Response(404, json={"detail": "Table was not found"})
        if path.endswith("/views/secret"):
            return httpx.Response(404, json={"detail": "View was not found"})
        return httpx.Response(500)

    catalog, _ = tool(handler)
    result = await catalog.execute(
        ctx=_Ctx(), action="describe", table="main.nyc.secret"
    )
    assert result.is_error and "404: View was not found" in text(result)


async def test_the_table_name_is_validated_before_it_reaches_sql():
    called = []
    catalog, _ = tool(
        lambda request: called.append(request) or httpx.Response(200, json={})
    )
    for bad in [
        "main.nyc.yellow; DROP TABLE x",
        "yellow",
        "main.nyc.",
        "main.nyc.y' OR '1'='1",
    ]:
        result = await catalog.execute(ctx=_Ctx(), action="describe", table=bad)
        assert result.is_error and "catalog.schema.table" in text(result)
    assert called == []


async def test_no_signed_in_tenant_means_no_access_and_no_request():
    called = []
    catalog, _ = tool(lambda request: called.append(request), _Tokens(token=None))
    result = await catalog.execute(ctx=_Ctx(), action="search", query="x")
    assert result.is_error and "tenant-scoped" in text(result) and called == []


async def test_an_unreachable_service_is_reported_not_raised():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    catalog, _ = tool(handler)
    result = await catalog.execute(ctx=_Ctx(), action="search", query="x")
    assert result.is_error and "unreachable" in text(result)


def test_the_tool_declares_itself_read_only_and_safe_to_run_in_parallel():
    from substrate.tools import ToolRisk

    assert DataCatalogTool.risk is ToolRisk.SAFE
    assert DataCatalogTool.idempotent and DataCatalogTool.concurrency_safe
    assert DataCatalogTool.input_schema["required"] == ["action"]
