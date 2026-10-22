"""`client.documents`: collections of documents and knowledge bases (Loamz Data's documents feature).

    docs = client.documents
    c = docs.ensure("tenants/acme/knowledge/handbook/library", semantic=True)      # idempotent; returns {"id", "name", ...}
    docs.add(c["id"], "policy.pdf", data=pdf_bytes)                                  # read by the data service's document server
    docs.search(c["id"], "refund window")["hits"]                                    # words and meaning, reranked server-side
    docs.outline(c["id"], document_id); docs.read(c["id"], document_id, section=2)

A collection is named by the agent library's key prefix (`tenants/{tenant}/…`); the service takes the tenant from the token.
Results are the service's JSON (dicts); the agents' data adapter turns them into storage's document types. The methods
are written once: on `AsyncDataClient` the same calls are awaited.
"""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from loamz.data._http import AsyncCore, SyncCore


@dataclass(frozen=True)
class Image:
    """A figure, chart or page picture and the facts about it."""

    data: bytes
    media_type: str
    document: str
    image: str
    page: int
    label: str
    caption: str | None


def _image(response: Any) -> Image:
    headers = response.headers
    caption = base64.b64decode(headers.get("x-picture-caption", "")).decode() or None
    return Image(
        data=response.content,
        media_type=headers.get("content-type", "image/png"),
        document=headers.get("x-picture-document", ""),
        image=headers.get("x-picture-image", ""),
        page=int(headers.get("x-picture-page", "0")),
        label=headers.get("x-picture-label", ""),
        caption=caption,
    )


def collection_id(name: str) -> str:
    """The id the service gives a collection name: the first 24 hex digits of its SHA-256 (a contract, so an adapter can address
    a collection without a round trip; `ensure` returns the same value)."""
    return hashlib.sha256(name.encode()).hexdigest()[:24]


def _q(value: str) -> str:
    return quote(value, safe="")


def _params(**values: Any) -> dict[str, Any]:
    return {k: v for k, v in values.items() if v is not None}


class _DocumentCalls:
    def _do(self, method: str, path: str, post: Callable | None = None, **kwargs: Any):
        raise NotImplementedError

    # ------------------------------------------------------------------ collections
    def ensure(self, name: str, *, semantic: bool = False):
        """Create the collection if it does not exist (idempotent). `semantic` embeds sections for meaning search."""
        return self._do("POST", "/collections:ensure", json={"name": name, "semantic": semantic})

    def collections(self, under: str = ""):
        """Collections at or below `under` (default: the tenant), each with its document count."""
        return self._do(
            "GET", "/collections", lambda b: b["collections"], params=_params(under=under or None)
        )

    def delete_collection(self, collection: str):
        return self._do("DELETE", f"/collections/{collection}")

    # ------------------------------------------------------------------ documents
    def add(
        self,
        collection: str,
        filename: str = "",
        *,
        data: bytes | None = None,
        extraction: dict[str, Any] | None = None,
        content_type: str | None = None,
        resource: str | None = None,
        metadata: dict[str, Any] | None = None,
        sha256: str | None = None,
    ):
        """File a document: raw `data` (read by the document server) or an `extraction` you already have."""
        body = {
            "filename": filename,
            "content_base64": base64.b64encode(data).decode() if data is not None else None,
            "extraction": extraction,
            "content_type": content_type,
            "resource": resource,
            "metadata": metadata,
            "sha256": sha256,
        }
        return self._do(
            "POST",
            f"/collections/{collection}/documents",
            json={k: v for k, v in body.items() if v is not None},
        )

    def list(self, collection: str, *, limit: int = 50, after: str | None = None):
        return self._do(
            "GET", f"/collections/{collection}/documents", params=_params(limit=limit, after=after)
        )

    def get(self, collection: str, document: str):
        return self._do("GET", f"/collections/{collection}/documents/{_q(document)}")

    def outline(self, collection: str, document: str, *, section: int | None = None):
        return self._do(
            "GET",
            f"/collections/{collection}/documents/{_q(document)}/outline",
            params=_params(section=section),
        )

    def read(
        self,
        collection: str,
        document: str,
        *,
        section: int | None = None,
        pages: tuple[int, int] | None = None,
        offset: int = 0,
    ):
        params = _params(section=section, offset=offset or None)
        if pages:
            params.update(first_page=pages[0], last_page=pages[1])
        return self._do(
            "GET", f"/collections/{collection}/documents/{_q(document)}/read", params=params
        )

    def section(self, collection: str, document: str, position: int, *, offset: int = 0):
        return self._do(
            "GET",
            f"/collections/{collection}/documents/{_q(document)}/sections/{position}",
            params=_params(offset=offset or None),
        )

    def image(
        self,
        collection: str,
        *,
        image: str | None = None,
        document: str | None = None,
        page: int | None = None,
    ):
        """A figure by id, or a page of a document, as an `Image`."""
        return self._do(
            "GET",
            f"/collections/{collection}/images",
            _image,
            params=_params(image=image, document=document, page=page),
            raw=True,
        )

    def delete(self, collection: str, document: str):
        return self._do("DELETE", f"/collections/{collection}/documents/{_q(document)}")

    def reindex(self, collection: str, *, missing_only: bool = False):
        """Rebuild the derived index from the bundle (the source of truth)."""
        return self._do(
            "POST",
            f"/collections/{collection}/documents:reindex",
            lambda b: b["reindexed"],
            json={"missing_only": missing_only},
        )

    def embed_missing(self, collection: str):
        return self._do(
            "POST", f"/collections/{collection}/documents:embed-missing", lambda b: b["embedded"]
        )

    # ------------------------------------------------------------------ search and topics
    def search(self, collection: str, query: str, *, document: str | None = None, limit: int = 8):
        """`{"hits": [...], "note": str | None}`: words and meaning, embedded and reranked by the service. `note` says
        when only words could be matched (the embedding service was unavailable)."""
        return self._do(
            "POST",
            f"/collections/{collection}:search",
            json=_params(query=query, document=document, limit=limit),
        )

    def topics(self, collection: str, topic: str = ""):
        return self._do(
            "GET", f"/collections/{collection}/topics", params=_params(topic=topic or None)
        )

    def topic_tree(self, collection: str):
        return self._do("GET", f"/collections/{collection}/topic-tree", lambda b: b["topics"])

    def set_topics(self, collection: str, plan: dict[str, list[str]], by: str):
        return self._do("PUT", f"/collections/{collection}/topics", json={"plan": plan, "by": by})

    # ------------------------------------------------------------------ enrichment (the model call stays with the caller)
    def pending(self, collection: str, *, enricher: dict[str, str] | None = None, limit: int = 50):
        return self._do(
            "POST",
            f"/collections/{collection}/enrichment:pending",
            lambda b: b["documents"],
            json=_params(enricher=enricher, limit=limit),
        )

    def pending_all(
        self,
        matching: str,
        *,
        excluding: str | None = None,
        enricher: dict[str, str] | None = None,
        limit: int = 100,
    ):
        return self._do(
            "POST",
            "/enrichment:pending",
            lambda b: b["documents"],
            json=_params(matching=matching, excluding=excluding, enricher=enricher, limit=limit),
        )

    def needs_reorganising(self, collection: str):
        return self._do(
            "GET", f"/collections/{collection}/needs-reorganising", lambda b: b["needed"]
        )

    def brief(self, collection: str, document: str):
        return self._do("GET", f"/collections/{collection}/documents/{_q(document)}/brief")

    def enrichment_state(self, collection: str, document: str):
        return self._do("GET", f"/collections/{collection}/documents/{_q(document)}/enrichment")

    def claim(self, collection: str, document: str, enricher: dict[str, str], attempts: int):
        return self._do(
            "POST",
            f"/collections/{collection}/documents/{_q(document)}/enrichment:claim",
            json={"enricher": enricher, "attempts": attempts},
        )

    def fail(
        self, collection: str, document: str, enricher: dict[str, str], attempts: int, error: str
    ):
        return self._do(
            "POST",
            f"/collections/{collection}/documents/{_q(document)}/enrichment:fail",
            json={"enricher": enricher, "attempts": attempts, "error": error},
        )

    def annotate(
        self,
        collection: str,
        document: str,
        *,
        card: str,
        sections: dict[int, str],
        topics: list[str],
        enricher: dict[str, str],
        attempts: int,
        usage: dict[str, Any] | None = None,
    ):
        return self._do(
            "POST",
            f"/collections/{collection}/documents/{_q(document)}/enrichment:annotate",
            json={
                "card": card,
                "sections": {str(k): v for k, v in sections.items()},
                "topics": topics,
                "enricher": enricher,
                "attempts": attempts,
                "usage": usage or {},
            },
        )


class Documents(_DocumentCalls):
    def __init__(self, core: SyncCore):
        self._core = core

    def _do(self, method, path, post=None, raw=False, **kwargs):
        if raw:
            body = self._core.check(self._core.send(method, path, **kwargs))
        else:
            body = self._core.call(method, path, **kwargs)
        return post(body) if post else body


class AsyncDocuments(_DocumentCalls):
    def __init__(self, core: AsyncCore):
        self._core = core

    async def _do(self, method, path, post=None, raw=False, **kwargs):
        if raw:
            body = await self._core.check(await self._core.send(method, path, **kwargs))
        else:
            body = await self._core.call(method, path, **kwargs)
        return post(body) if post else body
