"""/api/v1/imports: direct-to-storage uploads, then streaming ingest on attached compute.

1. `POST /imports` authorizes and returns presigned upload URLs (single PUT or multipart).
2. The client sends file bytes straight to object storage.
3. `POST /imports/{id}:complete` finishes the uploads and queues the work. A new table first gets
   its schema inferred (from a bounded sample), then data streams into it once, normalized and
   partitioned; the result is committed to Iceberg in one atomic operation.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from loamz.engine.errors import InvalidRequestError, NotFoundError, QuotaExceededError
from loamz.server.data.features.catalog.api import PartitionSpec, visible_schema, visible_table
from loamz.server.data.features.tables.materialized import forbid_writes
from loamz.server.data.features.tables.publications import job_publisher
from loamz.server.data.features.tables.staging import safe_name, staging_area
from loamz.server.data.kernel.container import Container
from loamz.server.data.kernel.errors import conflict, not_found
from loamz.server.data.kernel.http import Caller, Svc
from loamz.server.data.kernel.principal import Principal

router = APIRouter(prefix="/api/v1", tags=["imports"])
EXTENSIONS = {".csv": "csv", ".parquet": "parquet", ".ndjson": "ndjson", ".jsonl": "ndjson"}


class FileSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=255)
    size: int = Field(ge=1)


class ImportCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    table: str = Field(
        pattern=r"^[a-z_][a-z0-9_]{0,63}\.[a-z_][a-z0-9_]{0,63}\.[a-z_][a-z0-9_]{0,63}$"
    )
    mode: Literal["create", "append", "overwrite"] = "create"
    format: Literal["csv", "parquet", "ndjson"] | None = None
    partitioning: list[PartitionSpec] = Field(default_factory=list, max_length=8)
    files: list[FileSpec] = Field(min_length=1, max_length=1000)


class PartETag(BaseModel):
    model_config = ConfigDict(extra="forbid")
    part_number: int = Field(ge=1, le=10000)
    etag: str = Field(min_length=1, max_length=200)


class FileCompletion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(min_length=1, max_length=1024)
    parts: list[PartETag] = Field(default_factory=list, max_length=10000)


class ImportComplete(BaseModel):
    model_config = ConfigDict(extra="forbid")
    files: list[FileCompletion] = Field(default_factory=list, max_length=1000)


def file_format(body: ImportCreate) -> str:
    if body.format:
        return body.format
    found = set()
    for item in body.files:
        suffix = "." + item.name.rsplit(".", 1)[-1].lower() if "." in item.name else ""
        if suffix not in EXTENSIONS:
            raise InvalidRequestError(f"Cannot infer the format of {item.name}; set format")
        found.add(EXTENSIONS[suffix])
    if len(found) != 1:
        raise InvalidRequestError("All files in one import must share a format")
    return found.pop()


def public(record: dict) -> dict:
    target = record["target"]
    return {
        "id": record["id"],
        "table": f"{target['catalog']}.{target['schema']}.{target['table']}",
        "mode": record["mode"],
        "format": record["format"],
        "status": record["status"],
        "size_bytes": record["size_bytes"],
        "rows": record["rows"],
        "error": record["error"],
        "created_at": record["created_at"],
        "updated_at": record["updated_at"],
        "principal": record["principal"],
    }


@router.post("/imports", status_code=201)
async def create_import(
    body: ImportCreate, svc: Svc, caller: Caller, request: Request, response: Response
):
    catalog, schema, table = body.table.split(".")
    total = sum(f.size for f in body.files)
    if total > svc.settings.max_upload_bytes:
        raise QuotaExceededError("Import exceeds the configured size limit")
    parent = visible_schema(svc, caller, catalog, schema)
    if body.mode == "create":
        svc.store.require(caller, "schema", parent, "CREATE_TABLE")
        try:
            svc.store.table(catalog, schema, table)
        except NotFoundError:
            pass
        else:
            raise conflict(f"Table {body.table} already exists; use append or overwrite")
    else:
        if body.partitioning:
            raise InvalidRequestError("Partitioning is set when a table is created")
        record = visible_table(svc, caller, catalog, schema, table)
        svc.store.require(caller, "table", record, "MODIFY")
        forbid_writes(record)
    kind = file_format(body)
    record = svc.store.record(
        "imports",
        {
            "principal": caller.id,
            "target": {
                "catalog": catalog,
                "schema": schema,
                "table": table,
                "partitioning": [p.model_dump() for p in body.partitioning],
                "files": [],
            },
            "format": kind,
            "mode": body.mode,
            "status": "awaiting_upload",
            "size_bytes": total,
        },
    )
    uploads = []
    files = []
    for index, item in enumerate(body.files):
        key = f"imports/{record['id']}/{index:04d}-{safe_name(item.name)}"
        upload = await run_in_threadpool(svc.get(staging_area).begin, key, item.size)
        uploads.append({"name": item.name, **upload})
        files.append(
            {"key": key, "name": item.name, "size": item.size, "upload_id": upload.get("upload_id")}
        )
    svc.store.update("imports", record["id"], {"target": {**record["target"], "files": files}})
    svc.store.audit(
        caller,
        "import.create",
        body.table,
        "success",
        request.state.request_id,
        files=len(files),
        bytes=total,
    )
    response.headers["Location"] = f"/api/v1/imports/{record['id']}"
    return {
        "import": public(svc.store.get("imports", record["id"], ("target",))),
        "uploads": uploads,
    }


def owned(svc: Container, caller: Principal, import_id: str) -> dict:
    record = svc.store.get("imports", import_id, ("target",))
    if record["principal"] != caller.id and not caller.is_admin:
        raise not_found("Import")
    return record


@router.post("/imports/{import_id}:complete", status_code=202)
async def complete_import(
    import_id: str, body: ImportComplete, svc: Svc, caller: Caller, request: Request
):
    record = owned(svc, caller, import_id)
    if record["status"] != "awaiting_upload":
        raise conflict(f"Import is already {record['status']}")
    parts = {f.key: [p.model_dump() for p in f.parts] for f in body.files}
    total = 0
    for item in record["target"]["files"]:
        if item["upload_id"] and item["key"] not in parts:
            raise InvalidRequestError(f"Part ETags are required for {item['name']}")
        total += await run_in_threadpool(
            svc.get(staging_area).complete,
            item["key"],
            item["upload_id"],
            parts.get(item["key"], []),
        )
    svc.store.update("imports", import_id, {"status": "queued", "size_bytes": total})
    publisher = svc.get(job_publisher)
    target = record["target"]
    if record["mode"] == "create":
        job_id = await publisher.submit(
            "import.infer",
            "infer",
            {
                "sources": [svc.get(staging_area).uri(f["key"]) for f in target["files"]],
                "format": record["format"],
            },
            {"import_id": import_id},
        )
        svc.store.update("imports", import_id, {"job_id": job_id, "status": "inferring"})
    else:
        table = svc.store.table(target["catalog"], target["schema"], target["table"])
        await publisher.ingest(
            svc.store.get("imports", import_id, ("target",)), table, created_table=False
        )
    return public(svc.store.get("imports", import_id, ("target",)))


@router.get("/imports/{import_id}")
def get_import(import_id: str, svc: Svc, caller: Caller):
    return public(owned(svc, caller, import_id))


@router.get("/imports")
def list_imports(
    svc: Svc,
    caller: Caller,
    page_token: str | None = None,
    page_size: int = Query(50, ge=1, le=500),
):
    rows = svc.store.page(
        "imports", "principal=?", [caller.id], page_token, page_size + 1, ("target",)
    )
    more = len(rows) > page_size
    rows = rows[:page_size]
    return {
        "imports": [public(r) for r in rows],
        "next_page_token": f"{rows[-1]['created_at']}|{rows[-1]['id']}" if more else None,
    }
