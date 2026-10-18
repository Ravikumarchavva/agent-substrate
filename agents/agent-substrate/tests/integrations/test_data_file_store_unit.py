"""``DataFileStore`` without a network: key-to-volume mapping and the error mapping, against a scripted transport."""

from __future__ import annotations

import json

import httpx
import pytest

pytest.importorskip("substrate_data_client")

from substrate.integrations.data import DataFilesConnection, tenant_alias  # noqa: E402
from substrate.integrations.data._keys import split_key, split_prefix  # noqa: E402
from substrate.stores.files import WorkspacePathError, WorkspaceQuotaExceededError  # noqa: E402
from substrate.types import RunScope  # noqa: E402
from substrate.types.errors import (  # noqa: E402
    AuthError,
    ObjectNotFoundError,
    RateLimitedError,
    ScopeViolationError,
    ServiceUnavailableError,
)


def problem(status: int, kind: str, **extra) -> httpx.Response:
    return httpx.Response(
        status,
        json={
            "type": f"https://substrate.dev/problems/{kind}",
            "status": status,
            "detail": kind,
            **extra,
        },
        headers={"content-type": "application/problem+json"},
    )


def store_over(handler, **kw):
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    conn = DataFilesConnection(
        "http://data.test",
        tokens=lambda scope: f"token-for-{scope.user_id}",
        transport=httpx.MockTransport(record),
        **kw,
    )
    return conn.store(RunScope(tenant_id="acme-corp", user_id="u7")), seen, conn


def test_tenant_aliases_are_valid_catalog_names() -> None:
    assert tenant_alias("acme-corp") == "acme_corp"
    assert tenant_alias("42") == "t42"
    assert tenant_alias("A.B_c") == "a_b_c"


def test_keys_split_into_tenant_and_path_and_refuse_escapes() -> None:
    assert split_key("tenants/t/users/u/a.txt") == ("t", "users/u/a.txt")
    assert split_prefix("tenants/t/users/u/") == ("t", "users/u/")
    assert split_prefix("tenants/t") == ("t", "")
    for bad in (
        "users/u/a",
        "tenants//a",
        "tenants/t/../x",
        "tenants/t/a\\b",
        "tenants/t/a\x00",
    ):
        with pytest.raises(WorkspacePathError):
            split_key(bad)


async def test_every_call_carries_the_runs_token_and_the_tenants_volume() -> None:
    store, seen, conn = store_over(lambda r: problem(404, "not-found"))
    assert await store.exists("tenants/acme-corp/users/u7/a.txt") is False
    request = seen[0]
    assert request.headers["authorization"] == "Bearer token-for-u7"
    assert (
        request.url.path
        == "/api/v1/volumes/tenant_acme_corp.workspace.files/files/users/u7/a.txt"
    )
    await conn.aclose()


async def test_an_explicit_volume_mapping_wins() -> None:
    store, seen, conn = store_over(
        lambda r: problem(404, "not-found"), volumes={"acme-corp": "main.ws.acme"}
    )
    await store.exists("tenants/acme-corp/a")
    assert "/volumes/main.ws.acme/" in seen[0].url.path
    await conn.aclose()


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (problem(404, "not-found"), ObjectNotFoundError),
        (
            problem(403, "path-forbidden"),
            ObjectNotFoundError,
        ),  # reads never reveal that something exists
    ],
)
async def test_reads_of_absent_or_foreign_keys_are_not_found(
    response, expected
) -> None:
    store, _seen, conn = store_over(lambda r: response)
    with pytest.raises(expected):
        await store.info("tenants/acme-corp/users/other/x")
    assert await store.exists("tenants/acme-corp/users/other/x") is False
    assert await store.list_prefix("tenants/acme-corp/users/other/") == []
    await conn.aclose()


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (problem(403, "path-forbidden"), ScopeViolationError),
        (problem(403, "insufficient-scope", required=["files:write"]), AuthError),
        (problem(401, "unauthorized"), AuthError),
        (problem(400, "invalid-path"), WorkspacePathError),
        (
            problem(413, "quota-exceeded", used_bytes=9, quota_bytes=10),
            WorkspaceQuotaExceededError,
        ),
        (problem(503, "storage-unavailable"), ServiceUnavailableError),
        (problem(429, "rate-limited"), RateLimitedError),
    ],
)
async def test_failed_writes_become_the_engines_typed_errors(
    response, expected
) -> None:
    store, _seen, conn = store_over(lambda r: response)
    conn.client._core.retry = None  # type: ignore[attr-defined]  # no waiting out 429/503 here
    with pytest.raises(expected):
        await store.upload("tenants/acme-corp/users/u7/x", b"x")
    await conn.aclose()


async def test_the_listing_returns_full_keys_sizes_and_mtimes() -> None:
    page = {
        "files": [
            {
                "path": "users/u7/a.bin",
                "size": 5,
                "content_type": "x",
                "version": 1,
                "updated_at": "2026-01-01T00:00:00+00:00",
            }
        ],
        "directories": [],
        "next_page_token": None,
    }
    store, seen, conn = store_over(
        lambda r: httpx.Response(
            200, content=json.dumps(page), headers={"content-type": "application/json"}
        )
    )
    assert await store.list_prefix("tenants/acme-corp/users/u7/") == [
        ("tenants/acme-corp/users/u7/a.bin", 5, 1767225600.0)
    ]
    assert seen[0].url.params["prefix"] == "users/u7/"
    await conn.aclose()
