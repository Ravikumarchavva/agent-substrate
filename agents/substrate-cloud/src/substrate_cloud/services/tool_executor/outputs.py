"""Files a code-interpreter run produced, filed in the caller's own file store.

A file output arrives as bytes inside the tool result. Instead of passing a base64 blob on to the model, the executor stores it in
the caller's file store (Substrate Data's Files domain when the cell has one) beside the same file record the upload route writes, and
gives the model a stable ``file_ref`` it can open with the file manager. Nothing but the caller's token decides whose folder it is.
"""

from __future__ import annotations

import base64
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List

from substrate.workspace.layout import conversation_shared_key

from substrate_cloud.file_records import FileIndex, FileRecord
from substrate_cloud.shared.auth.claims import AuthClaims

logger = logging.getLogger(__name__)

_MIME = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "svg": "image/svg+xml",
    "pdf": "application/pdf",
    "csv": "text/csv",
    "json": "application/json",
    "txt": "text/plain",
    "html": "text/html",
    "zip": "application/zip",
}


def mime_for(filename: str, fmt: str = "") -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else fmt.lower()
    return _MIME.get(ext, "application/octet-stream")


def _safe_name(name: str) -> str:
    return name.replace("\\", "/").rsplit("/", 1)[-1] or "output"


async def save_file_outputs(
    result: Dict[str, Any], *, file_access: Any, user: AuthClaims, thread_id: uuid.UUID
) -> Dict[str, Any]:
    """Replace the file outputs of a tool result with references to files saved for ``user`` under ``thread_id``."""
    outputs: List[Dict[str, Any]] = result.get("metadata", {}).get("outputs", [])
    if not outputs:
        return result
    store = file_access.for_user(user.tenant_id, user.sub)
    index = FileIndex(store, user.tenant_id, user.sub)

    kept: List[Dict[str, Any]] = []
    refs: List[Dict[str, Any]] = []
    for item in outputs:
        if item.get("type") != "file":
            kept.append(item)
            continue
        name = _safe_name(item.get("name") or "output")
        raw = item.get("content", "")
        encoding = item.get("encoding", "utf-8")
        try:
            data = (
                base64.b64decode(raw) if encoding == "base64" else raw.encode(encoding)
            )
        except Exception as exc:
            logger.warning("Output %s could not be decoded: %s", name, exc)
            kept.append(item)
            continue
        file_id = uuid.uuid4()
        content_type = mime_for(name, item.get("format") or "")
        key = conversation_shared_key(
            user.tenant_id, user.sub, str(thread_id), f"outputs/{file_id}/{name}"
        )
        try:
            await store.upload(key, data, content_type=content_type)
            await index.save(
                FileRecord(
                    id=file_id,
                    tenant_id=user.tenant_id,
                    owner=user.sub,
                    object_key=key,
                    original_name=name,
                    content_type=content_type,
                    size_bytes=len(data),
                    thread_id=thread_id,
                    scope="outputs",
                    uploaded_at=datetime.now(timezone.utc),
                )
            )
        except (
            Exception
        ) as exc:  # the original output stays, so the model still has the bytes
            logger.warning("Output %s could not be saved: %s", name, exc)
            kept.append(item)
            continue
        ref = {
            "type": "file_ref",
            "file_id": str(file_id),
            "name": name,
            "content_type": content_type,
        }
        refs.append(ref)
        kept.append(ref)

    if not refs:
        return result
    names = ", ".join(r["name"] for r in refs)
    ids = ", ".join(r["file_id"] for r in refs)
    return {
        **result,
        "content": (result.get("content") or "")
        + f"\n\n[Files saved: {names}. Use file_manager to access file_ids: {ids}]",
        "metadata": {
            **result.get("metadata", {}),
            "outputs": kept,
            "saved_files": refs,
        },
    }
