"""A group's shared files: what is uploaded to it. They live under the owner's storage prefix (so the existing ``/files/object?key=`` route
serves them, with the same ownership check), in the workspace ``group-<id>``.

An agent does not open files: the text of a file is read when it is uploaded, and the start of it travels with the message (``excerpt``), so every
member sees what was shared in what it is shown, with no tool in between. Pictures cannot be read that way; the members are told one was shared.
"""

from __future__ import annotations

import mimetypes
import posixpath
from typing import Any

from substrate.workspace.layout import conversation_shared_key
from substrate_cloud.monolith.models import Group
from substrate_cloud.shared.auth.claims import AuthClaims

MAX_FILE_BYTES = 100 * 1024 * 1024
UPLOADS = "uploads"
# How much of a file's text goes along with the message (characters): enough for a note, a budget or a few pages, not a whole book.
EXCERPT_CHARS = 8000
_TEXT_TYPES = (
    "text/",
    "application/json",
    "application/xml",
    "application/x-yaml",
    "application/csv",
)


def prefix_of(tenant_id: str, user_id: str, workspace_id: str) -> str:
    """Where a workspace's files are in storage, with the trailing slash."""
    return conversation_shared_key(tenant_id, user_id, workspace_id, "x").removesuffix(
        "x"
    )


def shared_prefix(group: Group, claims: AuthClaims) -> str:
    return prefix_of(claims.tenant_id or "default", claims.sub, group.workspace_id)


def safe_name(name: str) -> str:
    return posixpath.basename(name.replace("\\", "/")).strip() or "file"


def record(
    prefix: str, relative: str, size: int, mime: str | None = None
) -> dict[str, Any]:
    """The record a message carries for one file under ``prefix``: enough to show it, fetch it, and tell an agent where it is."""
    return {
        "name": posixpath.basename(relative),
        "size": size,
        "mime": mime or mimetypes.guess_type(relative)[0] or "application/octet-stream",
        "key": prefix + relative,
    }


def attachment(
    group: Group, claims: AuthClaims, relative: str, size: int, mime: str | None = None
) -> dict[str, Any]:
    return record(shared_prefix(group, claims), relative, size, mime)


def _is_text(name: str, mime: str | None) -> bool:
    return bool(mime and mime.startswith(_TEXT_TYPES)) or name.lower().endswith(
        (
            ".txt",
            ".md",
            ".csv",
            ".json",
            ".yaml",
            ".yml",
            ".xml",
            ".log",
            ".py",
            ".js",
            ".ts",
            ".html",
            ".css",
            ".sql",
        )
    )


async def read_excerpt(
    reader: Any, data: bytes, name: str, mime: str | None
) -> dict[str, Any]:
    """The start of a file's text, for the agents: ``{"excerpt": …, "truncated": bool}``, or ``{}`` when it has none (a picture, a failed read)."""
    text = ""
    if _is_text(name, mime):
        text = data.decode("utf-8", errors="replace")
    elif reader is not None and not (mime or "").startswith(
        ("image/", "audio/", "video/")
    ):
        try:
            result = await reader.read(data, name)
            text = result.markdown if result.success else ""
        except Exception:  # noqa: BLE001 - a file nobody can read is shared without an excerpt, not refused
            text = ""
    text = text.strip()
    if not text:
        return {}
    return {"excerpt": text[:EXCERPT_CHARS], "truncated": len(text) > EXCERPT_CHARS}


PREVIEWS = ".previews"
# Past this a document is not opened just to draw its first page.
_MAX_PREVIEW_BYTES = 40 * 1024 * 1024


def _render_first_page(data: bytes) -> tuple[bytes, int] | None:
    """A PNG of a PDF's first page and its page count, or ``None`` when it cannot be drawn (not a PDF, locked, damaged)."""
    import io

    import pypdfium2 as pdfium

    try:
        pdf = pdfium.PdfDocument(data)
        try:
            pages = len(pdf)
            if pages == 0:
                return None
            image = pdf[0].render(scale=0.9).to_pil()
        finally:
            pdf.close()
        out = io.BytesIO()
        image.convert("RGB").save(out, "PNG", optimize=True)
        return out.getvalue(), pages
    except Exception:  # noqa: BLE001 - a PDF that cannot be drawn is shared without a preview
        return None


async def make_preview(
    store: Any, prefix: str, candidate: str, data: bytes, mime: str | None
) -> dict[str, Any]:
    """A thumbnail for a file that has no picture of its own: the first page of a PDF (kept beside the files, out of their list), with its page count."""
    if (mime != "application/pdf" and not candidate.lower().endswith(".pdf")) or len(
        data
    ) > _MAX_PREVIEW_BYTES:
        return {}
    import asyncio

    drawn = await asyncio.to_thread(_render_first_page, data)
    if drawn is None:
        return {}
    png, pages = drawn
    key = f"{prefix}{PREVIEWS}/{candidate}.png"
    await store.upload(key, png, content_type="image/png")
    return {"preview_key": key, "pages": pages}


async def unique_name(store: Any, folder: str, name: str) -> str:
    """``name``, or ``name (2)``, ``name (3)``… until it is not taken in ``folder`` (a storage prefix with its slash): never replacing a file."""
    taken = {key for key, _size, _mtime in await store.list_prefix(folder)}
    stem, dot, ext = name.rpartition(".") if "." in name else (name, "", "")
    candidate, n = name, 1
    while f"{folder}{candidate}" in taken:
        n += 1
        candidate = f"{stem} ({n}).{ext}" if dot else f"{name} ({n})"
    return candidate


async def save_upload(
    store: Any,
    group: Group,
    claims: AuthClaims,
    filename: str,
    data: bytes,
    mime: str | None,
    reader: Any = None,
) -> dict[str, Any]:
    """Store one upload in the group's files, never replacing a file that is already there ("report (2).pdf")."""
    prefix = shared_prefix(group, claims)
    candidate = await unique_name(store, f"{prefix}{UPLOADS}/", safe_name(filename))
    relative = f"{UPLOADS}/{candidate}"
    await store.upload(
        prefix + relative, data, content_type=mime or "application/octet-stream"
    )
    return {
        **attachment(group, claims, relative, len(data), mime),
        **await read_excerpt(reader, data, candidate, mime),
        **await make_preview(store, prefix, candidate, data, mime),
    }


async def list_files(
    store: Any, group: Group, claims: AuthClaims
) -> list[dict[str, Any]]:
    """Every file in the group, newest first."""
    prefix = shared_prefix(group, claims)
    rows = sorted(
        (
            r
            for r in await store.list_prefix(prefix)
            if not r[0].removeprefix(prefix).startswith(f"{PREVIEWS}/")
        ),
        key=lambda r: r[2],
        reverse=True,
    )
    return [
        {**attachment(group, claims, key.removeprefix(prefix), size), "modified": mtime}
        for key, size, mtime in rows
    ]


def belongs(group: Group, claims: AuthClaims, key: str) -> bool:
    """Whether ``key`` is a file of this group (a message may only attach what is already in the group's files)."""
    prefix = shared_prefix(group, claims)
    return key.startswith(prefix) and ".." not in key.removeprefix(prefix).split("/")
