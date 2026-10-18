"""`client.documents`, `.memory`, `.erasure` and `.exports` against a recording stand-in (no network): routes, bodies, sync and async."""

from __future__ import annotations

import asyncio
import base64
import json

import httpx
import pytest

from substrate_data_client import AsyncDataClient, DataClient, NotFound


class Recorder:
    def __init__(self, answer=None, status=200, headers=None):
        self.calls: list[tuple[str, str, dict, object]] = []
        self.answer, self.status, self.headers = (
            answer if answer is not None else {},
            status,
            headers,
        )

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.calls.append(
            (
                request.method,
                request.url.raw_path.decode().split("?")[0],
                dict(request.url.params),
                body,
            )
        )
        if self.status >= 400:
            return httpx.Response(
                self.status,
                json={"type": "https://substrate.dev/problems/not-found", "detail": "gone"},
            )
        if isinstance(self.answer, bytes):
            return httpx.Response(self.status, content=self.answer, headers=self.headers)
        return httpx.Response(self.status, json=self.answer)


def clients(recorder: Recorder):
    return (
        DataClient("http://data", token="t", transport=httpx.MockTransport(recorder), retry=None),
        AsyncDataClient(
            "http://data", token="t", transport=httpx.MockTransport(recorder), retry=None
        ),
    )


def both(recorder: Recorder, call):
    """Run `call(client)` on the sync and the async client; both must make the same request."""
    sync, asynchronous = clients(recorder)
    first = call(sync)

    async def go():
        result = call(asynchronous)
        return await result

    second = asyncio.run(go())
    assert recorder.calls[0] == recorder.calls[1]
    assert first == second
    return first


def test_collections_and_documents():
    rec = Recorder({"id": "c1", "name": "tenants/t/x", "semantic": True, "created": True})
    assert both(rec, lambda c: c.documents.ensure("tenants/t/x", semantic=True))["id"] == "c1"
    assert rec.calls[0] == (
        "POST",
        "/api/v1/collections:ensure",
        {},
        {"name": "tenants/t/x", "semantic": True},
    )

    rec = Recorder({"document": "d-1"})
    both(rec, lambda c: c.documents.add("c1", "a.pdf", data=b"%PDF", metadata={"k": 1}))
    method, path, _, body = rec.calls[0]
    assert (method, path) == ("POST", "/api/v1/collections/c1/documents")
    assert base64.b64decode(body["content_base64"]) == b"%PDF" and body["metadata"] == {"k": 1}
    assert "extraction" not in body

    rec = Recorder({"text": "t"})
    both(rec, lambda c: c.documents.read("c1", "d 1", section=2, pages=(3, 4), offset=10))
    assert rec.calls[0][1] == "/api/v1/collections/c1/documents/d%201/read"
    assert rec.calls[0][2] == {"section": "2", "offset": "10", "first_page": "3", "last_page": "4"}

    rec = Recorder({"hits": [], "note": None})
    both(rec, lambda c: c.documents.search("c1", "refunds", limit=3))
    assert rec.calls[0] == (
        "POST",
        "/api/v1/collections/c1:search",
        {},
        {"query": "refunds", "limit": 3},
    )

    rec = Recorder({"reindexed": 4})
    assert both(rec, lambda c: c.documents.reindex("c1", missing_only=True)) == 4


def test_an_image_comes_back_with_its_facts():
    headers = {
        "content-type": "image/png",
        "x-picture-document": "d",
        "x-picture-image": "fig1",
        "x-picture-page": "2",
        "x-picture-label": "chart",
        "x-picture-caption": base64.b64encode(b"Sales").decode(),
    }
    rec = Recorder(b"\x89PNG", headers=headers)
    sync, _ = clients(rec)
    image = sync.documents.image("c1", image="fig1")
    assert (image.data, image.page, image.label, image.caption) == (b"\x89PNG", 2, "chart", "Sales")


def test_enrichment_calls_carry_the_enricher():
    rec = Recorder({"documents": ["d1"]})
    assert both(
        rec, lambda c: c.documents.pending("c1", enricher={"name": "m", "version": "1"})
    ) == ["d1"]
    assert rec.calls[0][3] == {"enricher": {"name": "m", "version": "1"}, "limit": 50}
    rec = Recorder({})
    both(
        rec,
        lambda c: c.documents.annotate(
            "c1",
            "d1",
            card="x",
            sections={1: "s"},
            topics=["a/b"],
            enricher={"name": "m", "version": "1"},
            attempts=1,
        ),
    )
    assert rec.calls[0][3]["sections"] == {"1": "s"}


def test_memory_routes_and_owner_parameters():
    rec = Recorder({"matches": [{"record": {}, "score": 1}]})
    assert len(both(rec, lambda c: c.memory.query(user_id="u1", text_query="tea", limit=3))) == 1
    assert rec.calls[0][3] == {"namespace": {"user_id": "u1"}, "text_query": "tea", "limit": 3}
    rec = Recorder({"id": "r"})
    both(rec, lambda c: c.memory.save("r/1", {"content": []}))
    assert rec.calls[0][:2] == ("PUT", "/api/v1/memory/records/r%2F1")
    rec = Recorder(status=404)
    sync, _ = clients(rec)
    with pytest.raises(NotFound):
        sync.memory.get("r", user_id="u1")
    assert rec.calls[0][2] == {"user_id": "u1"}


def test_erasure_and_export_name_a_subject_or_the_whole_tenant():
    rec = Recorder({"status": "complete"})
    both(rec, lambda c: c.erasure.request("u1", tenant_id="t"))
    assert rec.calls[0] == (
        "POST",
        "/api/v1/erasure-requests",
        {},
        {"scope": "user", "subject": "u1", "tenant_id": "t"},
    )
    rec = Recorder({"id": "e"})
    both(rec, lambda c: c.exports.create(everything=True))
    assert rec.calls[0][3] == {"scope": "tenant"}
    sync, _ = clients(rec)
    with pytest.raises(ValueError):
        sync.erasure.request()
    with pytest.raises(ValueError):
        sync.exports.create("u1", everything=True)
