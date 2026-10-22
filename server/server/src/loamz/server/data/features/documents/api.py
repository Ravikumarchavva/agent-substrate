"""/api/v1/collections: documents and knowledge bases (docs/DOCUMENTS.md).

A *collection* is named by the agent library's key prefix, `tenants/{tenant}/…` (a conversation's documents, a knowledge
base). The token's verified tenant must be the one in the name and `users/{sub}/…` belongs to that subject, exactly as for
files; delegated tokens need `documents:read` / `documents:write`. The routes run the `Library` engine; the control plane
never parses an upload and the audit log records ids and counts only.
"""

from __future__ import annotations

import base64
import dataclasses
from dataclasses import dataclass
from typing import Annotated, Any

import orjson
from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from loamz.engine.documents import DocumentError, EnrichmentUsage, ExtractionResult
from loamz.engine.errors import (
    ForbiddenError,
    InvalidRequestError,
    LoamzError,
    QuotaExceededError,
    ServiceUnavailableError,
    VectorSpaceError,
)
from loamz.server.data.features.documents.engine import (
    CollectionRow,
    DocumentEngine,
    collection_id,
    digest,
)
from loamz.server.data.features.documents.volume import VolumeFileStore, tenant_volume
from loamz.server.data.features.files.service import files_service
from loamz.server.data.kernel.container import Container
from loamz.server.data.kernel.errors import not_found
from loamz.server.data.kernel.http import Caller, Svc, use
from loamz.server.data.kernel.principal import Principal
from loamz.server.data.kernel.scopes import require_scope

router = APIRouter(prefix="/api/v1", tags=["documents"])
READ = require_scope("documents:read", "documents:write")
WRITE = require_scope("documents:write")
MAX_NAME = 512
MAX_SEGMENT = 128


# ------------------------------------------------------------------ the domain object
class Documents:
    """The engine plus the checks that sit in front of it."""

    def __init__(self, engine: DocumentEngine, files: Any, *, grants: bool) -> None:
        self.engine = engine
        self.files = files  # FilesService: path policy, volumes
        self.grants = (
            grants  # check the tenant volume's READ/WRITE_VOLUME grants (volume-backed bundles)
        )

    def tenant_of(self, caller: Principal) -> str:
        if not caller.tenant_id:
            raise ForbiddenError("This token names no tenant, so it has no documents")
        return caller.tenant_id

    def authorize_name(
        self, svc: Container, caller: Principal, name: str, action: str
    ) -> tuple[str, str]:
        """`(tenant, path inside the tenant)` of a collection name the caller may use, else a refusal."""
        tenant = self.tenant_of(caller)
        rest = self.path_of(tenant, name)
        self.files.policy.authorize(caller, rest, action)
        if self.grants:
            volume = tenant_volume(self.files, tenant)
            svc.store.require(
                caller, "volume", volume, "WRITE_VOLUME" if action == "write" else "READ_VOLUME"
            )
        return tenant, rest

    @staticmethod
    def path_of(tenant: str, name: str) -> str:
        prefix = f"tenants/{tenant}/"
        if not name.startswith(prefix) or len(name) > MAX_NAME:
            raise not_found(
                "Collection"
            )  # another tenant's collection is indistinguishable from none
        rest = name[len(prefix) :]
        parts = rest.split("/")
        if not rest or any(
            not p
            or p in {".", ".."}
            or len(p) > MAX_SEGMENT
            or "\\" in p
            or any(ord(c) < 32 for c in p)
            for p in parts
        ):
            raise InvalidRequestError("Invalid collection name")
        return rest

    async def open(
        self, svc: Container, caller: Principal, cid: str, action: str
    ) -> tuple[CollectionRow, Any]:
        row = await self.engine.collection(cid)
        if row is None or row.tenant != caller.tenant_id:
            raise not_found("Collection")
        self.authorize_name(svc, caller, row.name, action)
        return row, self.engine.library(row)


def documents(container: Container) -> Documents:
    """Provider: the documents domain over the tenants' volumes (tests put one over local files)."""
    files = container.get(files_service)
    engine = DocumentEngine(container.settings, files=VolumeFileStore(files))
    container.on_close(engine.aclose)
    return Documents(engine, files, grants=True)


Domain = Annotated[Documents, Depends(use(documents))]


def audit(
    svc: Container, request: Request, caller: Principal, action: str, cid: str, **detail: Any
):
    svc.store.audit(
        caller, action, f"collection:{cid}", "success", request.state.request_id,
        collection_id=cid, **detail,
    )  # fmt: skip


def wire(value: Any) -> Any:
    """A library value as JSON-able data (dataclasses, tuples and all)."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: wire(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, dict | list | tuple):
        items = value.items() if isinstance(value, dict) else enumerate(value)
        return (
            {str(k): wire(v) for k, v in items}
            if isinstance(value, dict)
            else [wire(v) for _, v in items]
        )
    return value


@dataclass(frozen=True)
class NamedEnricher:
    name: str
    version: str


# ------------------------------------------------------------------ bodies
class Ensure(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=MAX_NAME)
    semantic: bool = False


class AddDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filename: str = Field(default="", max_length=255)
    content_base64: str | None = None
    extraction: dict[str, Any] | None = None
    content_type: str | None = Field(default=None, max_length=255)
    resource: str | None = Field(default=None, max_length=2048)
    metadata: dict[str, Any] | None = None
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class Search(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=2000)
    document: str | None = Field(default=None, max_length=300)
    limit: int = Field(default=8, ge=1, le=50)


class Reindex(BaseModel):
    model_config = ConfigDict(extra="forbid")
    missing_only: bool = False


class EnricherRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    version: str = Field(min_length=1, max_length=100)

    def stub(self) -> NamedEnricher:
        return NamedEnricher(self.name, self.version)


class PendingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enricher: EnricherRef | None = None
    limit: int = Field(default=50, ge=1, le=500)


class PendingAll(PendingRequest):
    matching: str = Field(max_length=MAX_NAME)
    excluding: str | None = Field(default=None, max_length=MAX_NAME)


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enricher: EnricherRef
    attempts: int = Field(ge=0, le=1000)


class Fail(Claim):
    error: str = Field(max_length=2000)


class Usage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cost_usd: float = Field(default=0.0, ge=0)
    calls: int = Field(default=0, ge=0)


class Annotate(Claim):
    card: str = Field(max_length=20_000)
    sections: dict[int, str] = Field(default_factory=dict)
    topics: list[str] = Field(default_factory=list, max_length=20)
    usage: Usage = Field(default_factory=Usage)


class SetTopics(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan: dict[str, list[str]]
    by: str = Field(min_length=1, max_length=200)


# ------------------------------------------------------------------ collections
@router.post("/collections:ensure", dependencies=[WRITE])
async def ensure_collection(
    body: Ensure, svc: Svc, caller: Caller, domain: Domain, request: Request, response: Response
):
    tenant, _rest = domain.authorize_name(svc, caller, body.name, "write")
    row, created = await domain.engine.ensure_collection(
        tenant, body.name, semantic=body.semantic, created_by=caller.id
    )
    if created:
        response.status_code = 201
        audit(svc, request, caller, "collection.ensure", row.id, semantic=row.semantic)
    return {"id": row.id, "name": row.name, "semantic": row.semantic, "created": created}


@router.get("/collections", dependencies=[READ])
async def list_collections(
    svc: Svc, caller: Caller, domain: Domain, under: str = Query("", max_length=MAX_NAME)
):
    """Collections at or below a name prefix (default: the caller's tenant), with their document counts."""
    tenant = domain.tenant_of(caller)
    prefix = under or f"tenants/{tenant}"
    if under:
        domain.authorize_name(svc, caller, under, "read")
    found = []
    for row in await domain.engine.collections_of(tenant, prefix=prefix):
        try:
            domain.authorize_name(svc, caller, row.name, "read")
        except LoamzError:
            continue  # another user's collection is not listed
        found.append({"id": row.id, "name": row.name, "semantic": row.semantic, "documents": 0})
    counts = dict(await domain.engine.lexical.collections(under=prefix))
    for item in found:
        item["documents"] = counts.get(item["name"], 0)
    return {"collections": found}


@router.delete("/collections/{cid}", dependencies=[WRITE])
async def delete_collection(cid: str, svc: Svc, caller: Caller, domain: Domain, request: Request):
    row, library = await domain.open(svc, caller, cid, "write")
    removed = await library.erase_under(row.name)
    await domain.engine.forget_collections([row.id])
    audit(svc, request, caller, "collection.delete", row.id, documents=removed)
    return {"documents_removed": removed}


# ------------------------------------------------------------------ documents
@router.post("/collections/{cid}/documents", dependencies=[WRITE])
async def add_document(
    cid: str, body: AddDocument, svc: Svc, caller: Caller, domain: Domain, request: Request,
    response: Response,
):  # fmt: skip
    row, library = await domain.open(svc, caller, cid, "write")
    if (body.content_base64 is None) == (body.extraction is None):
        raise InvalidRequestError("Send exactly one of content_base64 or extraction")
    limit = svc.settings.documents_max_upload_bytes
    if body.content_base64 is not None:
        if len(body.content_base64) > limit * 4 // 3 + 8:
            raise QuotaExceededError("The document exceeds the maximum size")
        if not domain.engine.can_read:
            raise ServiceUnavailableError("No document server is configured to read uploads")
        try:
            source: Any = base64.b64decode(body.content_base64, validate=True)
        except ValueError:
            raise InvalidRequestError("content_base64 is not valid base64") from None
    else:
        try:
            source = ExtractionResult.model_validate_json(orjson.dumps(body.extraction))
        except ValueError:
            raise InvalidRequestError("extraction is not a valid ExtractionResult") from None
    try:
        added = await library.add(
            source,
            body.filename,
            collection=row.name,
            resource=body.resource,
            content_type=body.content_type,
            metadata=body.metadata,
            sha256=body.sha256,
        )
    except DocumentError as exc:
        if "unavailable" in str(exc):
            raise ServiceUnavailableError(str(exc)[:500]) from None
        raise InvalidRequestError(f"The document could not be read: {str(exc)[:500]}") from None
    except VectorSpaceError as exc:
        raise VectorSpaceError(str(exc)[:500]) from None
    except ServiceUnavailableError:
        raise ServiceUnavailableError("The document server is unavailable") from None
    response.status_code = 200 if added.duplicate else 201
    if not added.duplicate:
        audit(
            svc, request, caller, "document.add", row.id,
            document_hash=digest(added.document), pages=added.pages, sections=added.sections,
            images=added.images,
        )  # fmt: skip
    return wire(added)


@router.get("/collections/{cid}/documents", dependencies=[READ])
async def list_documents(
    cid: str, svc: Svc, caller: Caller, domain: Domain,
    limit: int = Query(50, ge=1, le=200), after: str | None = Query(None, max_length=500),
):  # fmt: skip
    row, library = await domain.open(svc, caller, cid, "read")
    return wire(await library.list(collection=row.name, limit=limit, after=after))


@router.get("/collections/{cid}/documents/{document}", dependencies=[READ])
async def get_document(cid: str, document: str, svc: Svc, caller: Caller, domain: Domain):
    row, library = await domain.open(svc, caller, cid, "read")
    info = await library.info(row.name, document)
    if info is None:
        raise not_found("Document")
    return wire(info)


@router.get("/collections/{cid}/documents/{document}/outline", dependencies=[READ])
async def outline(
    cid: str, document: str, svc: Svc, caller: Caller, domain: Domain,
    section: int | None = Query(None, ge=0),
):  # fmt: skip
    row, library = await domain.open(svc, caller, cid, "read")
    found = await library.outline(collection=row.name, document=document, section=section)
    if found is None:
        raise not_found("Document")
    return wire(found)


@router.get("/collections/{cid}/documents/{document}/read", dependencies=[READ])
async def read_document(
    cid: str, document: str, svc: Svc, caller: Caller, domain: Domain,
    section: int | None = Query(None, ge=0), first_page: int | None = Query(None, ge=1),
    last_page: int | None = Query(None, ge=1), offset: int = Query(0, ge=0),
):  # fmt: skip
    row, library = await domain.open(svc, caller, cid, "read")
    pages = (first_page, last_page or first_page) if first_page else None
    found = await library.read(
        collection=row.name, document=document, section=section, pages=pages, offset=offset
    )
    if found is None:
        raise not_found("Document")
    return wire(found)


@router.get("/collections/{cid}/documents/{document}/sections/{section}", dependencies=[READ])
async def read_section(
    cid: str, document: str, section: int, svc: Svc, caller: Caller, domain: Domain,
    offset: int = Query(0, ge=0),
):  # fmt: skip
    row, library = await domain.open(svc, caller, cid, "read")
    found = await library.read(
        collection=row.name, document=document, section=section, offset=offset
    )
    if found is None:
        raise not_found("Section")
    return wire(found)


@router.get("/collections/{cid}/images", dependencies=[READ])
async def images(
    cid: str, svc: Svc, caller: Caller, domain: Domain,
    image: str | None = Query(None, max_length=200), document: str | None = Query(None, max_length=300),
    page: int | None = Query(None, ge=1),
):  # fmt: skip
    """A figure, chart or page image as bytes; the facts about it travel as `x-picture-*` headers."""
    row, library = await domain.open(svc, caller, cid, "read")
    picture = await library.view(collection=row.name, image=image, document=document, page=page)
    if picture is None:
        raise not_found("Image")
    headers = {
        "x-picture-document": picture.document,
        "x-picture-image": picture.image,
        "x-picture-page": str(picture.page),
        "x-picture-label": picture.label,
        "x-picture-caption": base64.b64encode((picture.caption or "").encode()).decode(),
    }
    return Response(picture.data, media_type=picture.media_type, headers=headers)


@router.delete("/collections/{cid}/documents/{document}", dependencies=[WRITE])
async def delete_document(
    cid: str, document: str, svc: Svc, caller: Caller, domain: Domain, request: Request
):
    row, library = await domain.open(svc, caller, cid, "write")
    if not await library.delete(collection=row.name, document=document):
        raise not_found("Document")
    audit(svc, request, caller, "document.delete", row.id, document_hash=digest(document))
    return Response(status_code=204)


@router.post("/collections/{cid}/documents:reindex", dependencies=[WRITE])
async def reindex(
    cid: str, body: Reindex, svc: Svc, caller: Caller, domain: Domain, request: Request
):
    row, library = await domain.open(svc, caller, cid, "write")
    count = await library.reindex(collection=row.name, missing_only=body.missing_only)
    audit(
        svc,
        request,
        caller,
        "collection.reindex",
        row.id,
        documents=count,
        missing_only=body.missing_only,
    )
    return {"reindexed": count}


@router.post("/collections/{cid}/documents:embed-missing", dependencies=[WRITE])
async def embed_missing(cid: str, svc: Svc, caller: Caller, domain: Domain, request: Request):
    row, library = await domain.open(svc, caller, cid, "write")
    count = await library.embed_missing(collection=row.name)
    audit(svc, request, caller, "collection.embed", row.id, chunks=count)
    return {"embedded": count}


# ------------------------------------------------------------------ search and topics
@router.post("/collections/{cid}:search", dependencies=[READ])
async def search(cid: str, body: Search, svc: Svc, caller: Caller, domain: Domain):
    """Words, and meaning when the collection is semantic. Embedding and reranking happen here; a service that
    is down degrades the answer to words and says so in `note`."""
    row, library = await domain.open(svc, caller, cid, "read")
    hits = await library.find(
        collection=row.name, query=body.query, document=body.document, limit=body.limit
    )
    return {"hits": [wire(h) for h in hits], "note": hits.note}


@router.get("/collections/{cid}/topics", dependencies=[READ])
async def browse(
    cid: str, svc: Svc, caller: Caller, domain: Domain, topic: str = Query("", max_length=500)
):
    row, library = await domain.open(svc, caller, cid, "read")
    found = await library.browse(collection=row.name, topic=topic)
    if found is None:
        raise not_found("Topic")
    return wire(found)


@router.get("/collections/{cid}/topic-tree", dependencies=[READ])
async def topic_tree(cid: str, svc: Svc, caller: Caller, domain: Domain):
    row, library = await domain.open(svc, caller, cid, "read")
    return {"topics": await library.topic_tree(collection=row.name)}


@router.put("/collections/{cid}/topics", dependencies=[WRITE])
async def set_topics(
    cid: str, body: SetTopics, svc: Svc, caller: Caller, domain: Domain, request: Request
):
    row, library = await domain.open(svc, caller, cid, "write")
    await library.set_topics(collection=row.name, plan=body.plan, by=body.by)
    audit(svc, request, caller, "collection.topics", row.id, documents=len(body.plan))
    return Response(status_code=204)


# ------------------------------------------------------------------ enrichment (the model call stays with the caller)
@router.post("/collections/{cid}/enrichment:pending", dependencies=[READ])
async def pending(cid: str, body: PendingRequest, svc: Svc, caller: Caller, domain: Domain):
    row, library = await domain.open(svc, caller, cid, "read")
    enricher = body.enricher.stub() if body.enricher else None
    return {
        "documents": await library.pending(collection=row.name, enricher=enricher, limit=body.limit)
    }


@router.post("/enrichment:pending", dependencies=[READ])
async def pending_all(body: PendingAll, svc: Svc, caller: Caller, domain: Domain):
    """`(collection, document)` pairs across the caller's tenant that still need describing."""
    tenant = domain.tenant_of(caller)
    if not body.matching.startswith(f"tenants/{tenant}/"):
        raise InvalidRequestError("matching must start with the caller's tenant prefix")
    enricher = body.enricher.stub() if body.enricher else None
    found = await domain.engine.semantic.pending_all(
        enricher=enricher, limit=body.limit, matching=body.matching, excluding=body.excluding
    )
    items = []
    for name, document in found:
        try:
            domain.authorize_name(svc, caller, name, "read")
        except LoamzError:
            continue  # another tenant's (or user's) pattern match is not ours
        items.append(
            {"collection": name, "collection_id": collection_id(name), "document": document}
        )
    return {"documents": items}


@router.get("/collections/{cid}/documents/{document}/brief", dependencies=[READ])
async def brief(cid: str, document: str, svc: Svc, caller: Caller, domain: Domain):
    row, library = await domain.open(svc, caller, cid, "read")
    found = await library.brief(collection=row.name, document=document)
    if found is None:
        raise not_found("Document")
    return wire(found)


@router.get("/collections/{cid}/documents/{document}/enrichment", dependencies=[READ])
async def enrichment_state(cid: str, document: str, svc: Svc, caller: Caller, domain: Domain):
    row, library = await domain.open(svc, caller, cid, "read")
    found = await library.enrichment_state(collection=row.name, document=document)
    if found is None:
        raise not_found("Enrichment")
    return wire(found)


@router.get("/collections/{cid}/needs-reorganising", dependencies=[READ])
async def needs_reorganising(cid: str, svc: Svc, caller: Caller, domain: Domain):
    row, library = await domain.open(svc, caller, cid, "read")
    return {"needed": await library.needs_reorganising(row.name)}


@router.post("/collections/{cid}/documents/{document}/enrichment:claim", dependencies=[WRITE])
async def claim(cid: str, document: str, body: Claim, svc: Svc, caller: Caller, domain: Domain):
    row, library = await domain.open(svc, caller, cid, "write")
    await library.claim(
        collection=row.name,
        document=document,
        enricher=body.enricher.stub(),
        attempts=body.attempts,
    )
    return Response(status_code=204)


@router.post("/collections/{cid}/documents/{document}/enrichment:fail", dependencies=[WRITE])
async def fail(cid: str, document: str, body: Fail, svc: Svc, caller: Caller, domain: Domain):
    row, library = await domain.open(svc, caller, cid, "write")
    await library.fail(
        collection=row.name, document=document, enricher=body.enricher.stub(),
        attempts=body.attempts, error=body.error,
    )  # fmt: skip
    return Response(status_code=204)


@router.post("/collections/{cid}/documents/{document}/enrichment:annotate", dependencies=[WRITE])
async def annotate(
    cid: str, document: str, body: Annotate, svc: Svc, caller: Caller, domain: Domain,
    request: Request,
):  # fmt: skip
    row, library = await domain.open(svc, caller, cid, "write")
    try:
        await library.annotate(
            collection=row.name, document=document, card=body.card, sections=body.sections,
            topics=body.topics, enricher=body.enricher.stub(), attempts=body.attempts,
            usage=EnrichmentUsage(**body.usage.model_dump()),
        )  # fmt: skip
    except DocumentError:
        raise not_found("Document") from None
    audit(svc, request, caller, "document.annotate", row.id, document_hash=digest(document))
    return Response(status_code=204)
