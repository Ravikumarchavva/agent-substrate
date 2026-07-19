"""ONLYOFFICE Document Server integration — editable Office files in the panel.

The browser embeds the ONLYOFFICE editor (an iframe served from
``ONLYOFFICE_URL``) using the JWT-signed config from ``GET /config``. The doc
server itself (no Bearer) fetches the file from ``GET /download`` (short-lived
signed token) and POSTs the edited file back to ``POST /callback`` (validated
via ONLYOFFICE's shared-secret JWT). Saves flow through the same ``FileVersion``
lineage as ``routes/workspace.py``, so a human's ONLYOFFICE edit and the agent's
code_interpreter rewrite reconcile instead of clobbering.

Networking (two directions, like CODE_INTERPRETER_URL vs *_EXTERNAL):
  browser → doc server        : ONLYOFFICE_URL (loads the editor API)
  doc server → this backend   : ONLYOFFICE_INTERNAL_CALLBACK_BASE (document.url,
                                callbackUrl — must be reachable *from* the
                                doc-server container, e.g. host.docker.internal)
  backend → doc server        : to fetch the edited file the callback points at;
                                its host is rewritten to ONLYOFFICE_URL.
"""

from __future__ import annotations

import mimetypes
import re
import time
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx
import jwt
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from substrate.capabilities.storage.workspace import WorkspacePathError
from substrate.logger import setup_logging
from substrate.serving.monolith.database import get_db
from substrate.serving.monolith.dependencies import ServerDependencies, get_ctx
from substrate.serving.monolith.file_versioning import (
    capture_bytes,
    record_version,
    sha256_hex,
)
from substrate.serving.monolith.routes.workspace import (
    _require_workspace_store,
    _resolve_session_key,
    _session_key,
    _session_rel,
)
from substrate.serving.monolith.security.deps import get_current_user
from substrate.serving.shared.auth.claims import AuthClaims
from substrate.serving.shared.settings import settings

logger = setup_logging()

router = APIRouter(prefix="/workspace/onlyoffice", tags=["onlyoffice"])

# Extension → ONLYOFFICE documentType.
_DOC_TYPE: dict[str, str] = {
    "docx": "word",
    "doc": "word",
    "odt": "word",
    "rtf": "word",
    "txt": "word",
    "xlsx": "cell",
    "xls": "cell",
    "ods": "cell",
    "csv": "cell",
    "pptx": "slide",
    "ppt": "slide",
    "odp": "slide",
}


def _ext(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def _sign_file_token(sub: str, thread_id: str, path: str) -> str:
    """Short-lived token authorizing the doc server to fetch/callback for one
    specific file (it can't send our normal Bearer). Signed with JWT_SECRET —
    this is ours, distinct from the ONLYOFFICE shared secret."""
    return jwt.encode(
        {
            "sub": sub,
            "thread_id": thread_id,
            "path": path,
            "type": "oo_file",
            "exp": int(time.time()) + 24 * 3600,
        },
        settings.JWT_SECRET,
        algorithm="HS256",
    )


def _verify_file_token(token: str) -> dict[str, Any]:
    try:
        claims = jwt.decode(token, settings.JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid token") from exc
    if claims.get("type") != "oo_file":
        raise HTTPException(status_code=401, detail="Invalid token")
    return claims


def _doc_key(thread_id: str, name: str, checksum: str) -> str:
    """A per-version key ONLYOFFICE uses to cache/reload: changing the checksum
    (agent rewrote the file) yields a new key → the editor reloads. Keep to the
    allowed charset [0-9a-zA-Z._=-] and ≤ 128 chars."""
    raw = f"{thread_id}_{name}_{checksum[:16]}"
    return re.sub(r"[^0-9a-zA-Z._=-]", "_", raw)[:128]


def _reachable_edited_url(url: str) -> str:
    """The callback's edited-file URL points at the doc server's own address;
    rewrite its origin to ONLYOFFICE_URL so this backend can fetch it."""
    if not settings.ONLYOFFICE_URL:
        return url
    base = urlparse(settings.ONLYOFFICE_URL)
    u = urlparse(url)
    return urlunparse((base.scheme, base.netloc, u.path, u.params, u.query, u.fragment))


class OnlyOfficeConfigResponse(BaseModel):
    # Browser loads {url}/web-apps/apps/api/documents/api.js, then
    # new DocsAPI.DocEditor(el, config).
    url: str
    config: dict[str, Any]


@router.get("/config", response_model=OnlyOfficeConfigResponse)
async def get_config(
    thread_id: str = Query(...),
    path: str = Query(...),
    mode: str = Query("edit", pattern="^(edit|view)$"),
    claims: AuthClaims = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    ctx: ServerDependencies = Depends(get_ctx),
) -> OnlyOfficeConfigResponse:
    if not settings.ONLYOFFICE_URL or not settings.ONLYOFFICE_JWT_SECRET:
        raise HTTPException(status_code=503, detail="ONLYOFFICE is not configured")
    store = _require_workspace_store(ctx)
    # Resolve the ref to the real file the model wrote (it may have saved into a
    # subdir like out/ but referenced the bare name) — see _resolve_session_key.
    key = _resolve_session_key(store, claims.sub, thread_id, path)
    try:
        data = await store.download(key)
    except (WorkspacePathError, KeyError, FileNotFoundError):
        raise HTTPException(status_code=404, detail="File not found") from None

    name = key.rsplit("/", 1)[-1]
    ext = _ext(name)
    doc_type = _DOC_TYPE.get(ext)
    if doc_type is None:
        raise HTTPException(status_code=400, detail=f"Unsupported type: .{ext}")

    # Lazy-capture an agent change / the initial state before editing.
    await capture_bytes(
        db, store, object_key=key, data=data, user_id=claims.sub, thread_id=thread_id
    )

    # Sign the *resolved* path so download/callback address the exact file.
    token = _sign_file_token(
        claims.sub, thread_id, _session_rel(key, claims.sub, thread_id)
    )
    base = settings.ONLYOFFICE_INTERNAL_CALLBACK_BASE.rstrip("/")
    config: dict[str, Any] = {
        "document": {
            "fileType": ext,
            "key": _doc_key(thread_id, name, sha256_hex(data)),
            "title": name,
            "url": f"{base}/workspace/onlyoffice/download?token={token}",
            "permissions": {"edit": mode == "edit", "download": True},
        },
        "documentType": doc_type,
        "editorConfig": {
            "mode": mode,
            "lang": "en",
            "callbackUrl": f"{base}/workspace/onlyoffice/callback?token={token}",
            "user": {"id": claims.sub, "name": claims.email or claims.sub},
        },
    }
    config["token"] = jwt.encode(
        config, settings.ONLYOFFICE_JWT_SECRET, algorithm="HS256"
    )
    return OnlyOfficeConfigResponse(
        url=settings.ONLYOFFICE_URL.rstrip("/"), config=config
    )


@router.get("/download")
async def download(
    token: str = Query(...),
    ctx: ServerDependencies = Depends(get_ctx),
) -> StreamingResponse:
    """Stream a file to the doc server. Token-gated (no Bearer — the doc server
    can't send one); the signed token scopes it to exactly one file."""
    claims = _verify_file_token(token)
    store = _require_workspace_store(ctx)
    key = _session_key(claims["sub"], claims["thread_id"], claims["path"])
    try:
        data = await store.download(key)
    except (WorkspacePathError, KeyError, FileNotFoundError):
        raise HTTPException(status_code=404, detail="File not found") from None
    name = key.rsplit("/", 1)[-1]
    content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"

    async def _stream():
        yield data

    return StreamingResponse(
        _stream(),
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.post("/callback")
async def callback(
    request: Request,
    token: str = Query(...),
    db: AsyncSession = Depends(get_db),
    ctx: ServerDependencies = Depends(get_ctx),
) -> dict[str, int]:
    """Save-back from the doc server. status 2 (ready) / 6 (force-save while
    editing) carry a URL to the edited file: fetch it, snapshot the current
    canonical, write the edit, record a ``"user"`` version. Must return
    ``{"error": 0}`` on success or ONLYOFFICE retries/marks the doc broken."""
    claims = _verify_file_token(token)
    body = await request.json()

    # Validate ONLYOFFICE's own JWT on the callback body.
    if settings.ONLYOFFICE_JWT_SECRET:
        body_token = body.get("token")
        if not body_token:
            raise HTTPException(status_code=401, detail="Missing ONLYOFFICE token")
        try:
            jwt.decode(body_token, settings.ONLYOFFICE_JWT_SECRET, algorithms=["HS256"])
        except jwt.PyJWTError as exc:
            raise HTTPException(status_code=401, detail="Bad ONLYOFFICE token") from exc

    status = body.get("status")
    if status in (2, 6):
        edited_url = body.get("url")
        if edited_url:
            try:
                async with httpx.AsyncClient(timeout=60.0) as client:
                    resp = await client.get(_reachable_edited_url(edited_url))
                    resp.raise_for_status()
                    new_bytes = resp.content
            except httpx.HTTPError as exc:
                logger.error("ONLYOFFICE callback fetch failed: %s", exc)
                return {"error": 1}

            store = _require_workspace_store(ctx)
            key = _session_key(claims["sub"], claims["thread_id"], claims["path"])
            try:
                current = await store.download(key)
                await capture_bytes(
                    db,
                    store,
                    object_key=key,
                    data=current,
                    user_id=claims["sub"],
                    thread_id=claims["thread_id"],
                )
            except (WorkspacePathError, KeyError, FileNotFoundError):
                pass
            await store.upload(key, new_bytes)
            await record_version(
                db,
                store,
                object_key=key,
                data=new_bytes,
                author="user",
                user_id=claims["sub"],
                thread_id=claims["thread_id"],
            )
            logger.info("ONLYOFFICE saved %s (%d bytes)", key, len(new_bytes))

    return {"error": 0}
