"""Governed volumes and model registrations; control plane only signs object access."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Literal
from urllib.parse import urlsplit
from uuid import uuid4

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from loamz.data.server.features.catalog.api import Name, audit, owns, visible_schema
from loamz.data.server.features.tables.staging import staging_area
from loamz.data.server.kernel.errors import not_found
from loamz.data.server.kernel.http import Caller, Svc, decode_token, encode_token, page_size
from loamz.data.storage.errors import InvalidRequestError, ServiceUnavailableError

router = APIRouter(prefix="/api/v1", tags=["catalog assets"])
Kind = Literal["volumes", "models"]


class AssetCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Name
    comment: str | None = Field(default=None, max_length=1000)
    artifact_uri: str | None = Field(default=None, max_length=2000)
    framework: str | None = Field(default=None, max_length=100)

    @field_validator("artifact_uri")
    @classmethod
    def artifact_location(cls, value):
        if value is not None:
            location = urlsplit(value)
            if (
                location.scheme not in {"s3", "https"}
                or not location.netloc
                or location.username
                or location.password
                or location.query
                or location.fragment
            ):
                raise ValueError(
                    "Artifact URI must be an s3:// or https:// location without credentials or query parameters"
                )
        return value


def visible(svc, caller, kind, catalog, schema, name):
    record = svc.store.asset(kind, catalog, schema, name)
    if not svc.store.can_browse(caller, kind, record):
        raise not_found(f"{kind} {catalog}.{schema}.{name}")
    return record


def public(record):
    return {
        key: record.get(key)
        for key in (
            "id",
            "kind",
            "name",
            "owner",
            "comment",
            "properties",
            "created_at",
            "updated_at",
            "version",
        )
    } | {"full_name": f"{record['catalog_name']}.{record['schema_name']}.{record['name']}"}


@router.get("/catalogs/{catalog}/schemas/{schema}/{collection}")
def listing(
    catalog: str,
    schema: str,
    collection: Kind,
    svc: Svc,
    caller: Caller,
    page_token: str | None = None,
    page_size_: int | None = Query(None, alias="page_size"),
):
    visible_schema(svc, caller, catalog, schema)
    kind = "volume" if collection == "volumes" else "model"
    limit = page_size(page_size_)
    # Cursor names must come from visible objects; hidden objects must not leak via tokens.
    cursor = decode_token(page_token)
    visible_records = []
    while len(visible_records) <= limit:
        records = svc.store.list_assets(kind, catalog, schema, cursor, limit + 1)
        visible_records.extend(
            record for record in records if svc.store.can_browse(caller, kind, record)
        )
        if len(records) < limit + 1:
            break
        cursor = records[-1]["name"]
    page = visible_records[:limit]
    return {
        collection: [public(record) for record in page],
        "next_page_token": encode_token(page[-1]["name"]) if len(visible_records) > limit else None,
    }


@router.post("/catalogs/{catalog}/schemas/{schema}/{collection}", status_code=201)
def create(
    catalog: str,
    schema: str,
    collection: Kind,
    body: AssetCreate,
    svc: Svc,
    caller: Caller,
    request: Request,
):
    parent = visible_schema(svc, caller, catalog, schema)
    kind = "volume" if collection == "volumes" else "model"
    svc.store.require(
        caller, "schema", parent, "CREATE_VOLUME" if kind == "volume" else "CREATE_MODEL"
    )
    if kind == "model" and not body.artifact_uri:
        raise InvalidRequestError("A model artifact URI is required")
    if kind == "volume" and (body.artifact_uri or body.framework):
        raise InvalidRequestError("Volumes use their own managed storage prefix")
    properties = (
        {"bucket": svc.settings.staging_bucket, "prefix": f"volumes/{uuid4().hex}/"}
        if kind == "volume"
        else {"artifact_uri": body.artifact_uri, "framework": body.framework, "model_version": 1}
    )
    record = svc.store.create_asset(
        kind,
        parent,
        {"name": body.name, "owner": caller.id, "comment": body.comment, "properties": properties},
    )
    audit(svc, request, caller, f"{kind}.create", f"{catalog}.{schema}.{body.name}")
    return public(svc.store.asset(kind, catalog, schema, record["name"]))


@router.get("/catalogs/{catalog}/schemas/{schema}/{collection}/{name}")
def get_asset(catalog: str, schema: str, collection: Kind, name: str, svc: Svc, caller: Caller):
    return public(
        visible(
            svc, caller, "volume" if collection == "volumes" else "model", catalog, schema, name
        )
    )


@router.delete("/catalogs/{catalog}/schemas/{schema}/{collection}/{name}", status_code=204)
def unregister(
    catalog: str,
    schema: str,
    collection: Kind,
    name: str,
    svc: Svc,
    caller: Caller,
    request: Request,
):
    kind = "volume" if collection == "volumes" else "model"
    record = visible(svc, caller, kind, catalog, schema, name)
    owns(svc, caller, kind, record)
    svc.store.delete_securable(kind, record, record["version"])
    audit(svc, request, caller, f"{kind}.unregister", f"{catalog}.{schema}.{name}")


def object_key(record, path):
    if (
        path.startswith("/")
        or "\\" in path
        or "\x00" in path
        or any(part in {".", ".."} for part in path.split("/"))
    ):
        raise InvalidRequestError("Path must stay inside the volume")
    return (
        record["properties"]["prefix"] + str(PurePosixPath(path))
        if path
        else record["properties"]["prefix"]
    )


@router.get("/catalogs/{catalog}/schemas/{schema}/volumes/{name}/files")
def files(
    catalog: str,
    schema: str,
    name: str,
    svc: Svc,
    caller: Caller,
    path: str = Query("", max_length=512),
    continuation: str | None = Query(None, max_length=2000),
):
    record = visible(svc, caller, "volume", catalog, schema, name)
    svc.store.require(caller, "volume", record, "READ_VOLUME")
    prefix = object_key(record, path)
    if not prefix.endswith("/"):
        prefix += "/"
    try:
        result = svc.get(staging_area).s3.list_objects_v2(
            Bucket=record["properties"]["bucket"],
            Prefix=prefix,
            Delimiter="/",
            MaxKeys=200,
            **({"ContinuationToken": continuation} if continuation else {}),
        )
    except ClientError, BotoCoreError:
        raise ServiceUnavailableError("Volume storage is unavailable") from None
    root = record["properties"]["prefix"]
    return {
        "files": [
            {
                "path": item["Key"][len(root) :],
                "name": item["Key"].rsplit("/", 1)[-1],
                "size_bytes": item["Size"],
                "modified_at": item["LastModified"],
                "directory": False,
            }
            for item in result.get("Contents", [])
        ],
        "directories": [
            {"path": item["Prefix"][len(root) :].rstrip("/"), "directory": True}
            for item in result.get("CommonPrefixes", [])
        ],
        "continuation": result.get("NextContinuationToken"),
    }


class ObjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=512)
    action: Literal["upload", "download"]
    size_bytes: int = Field(default=0, ge=0, le=64 * 1024 * 1024)


@router.post("/catalogs/{catalog}/schemas/{schema}/volumes/{name}/access")
def object_access(
    catalog: str,
    schema: str,
    name: str,
    body: ObjectRequest,
    svc: Svc,
    caller: Caller,
    request: Request,
):
    record = visible(svc, caller, "volume", catalog, schema, name)
    svc.store.require(
        caller, "volume", record, "WRITE_VOLUME" if body.action == "upload" else "READ_VOLUME"
    )
    key = object_key(record, body.path)
    params = {"Bucket": record["properties"]["bucket"], "Key": key}
    if body.action == "upload":
        params["ContentLength"] = body.size_bytes
    try:
        url = svc.get(staging_area).s3.generate_presigned_url(
            "put_object" if body.action == "upload" else "get_object", Params=params, ExpiresIn=300
        )
    except ClientError, BotoCoreError:
        raise ServiceUnavailableError("Volume storage is unavailable") from None
    audit(
        svc, request, caller, f"volume.{body.action}", f"{catalog}.{schema}.{name}", path=body.path
    )
    return {"url": url, "method": "PUT" if body.action == "upload" else "GET", "expires_in": 300}
