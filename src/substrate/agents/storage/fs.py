"""Filesystem helpers shared by every ``Local*`` store in ``agents/``: crash-safe
JSON writes, and turning an external id into one safe path component."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from urllib.parse import quote


def atomic_write_json(path: Path, data: object) -> None:
    """Write *data* as JSON to *path* atomically (temp file, then rename) so a
    crash mid-write never leaves a truncated file behind."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=".tmp_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, default=str)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def safe_name(identifier: str) -> str:
    """Encode an external id (session, branch, collection, tenant, ...) as a
    single path component.

    Ids reach these stores from request bodies and other untrusted sources, so
    they must never be able to contain a path separator or be ``.``/``..`` —
    that would read, write or ``rmtree`` outside the store's root. Percent-
    encoding is injective (two ids never share a file, unlike ``basename``) and
    leaves ordinary ids — UUIDs, hex, ``[A-Za-z0-9._~-]`` — exactly as they
    were, so existing data keeps its names. ``urllib.parse.unquote`` reverses it.
    """
    if not identifier:
        raise ValueError("identifier must not be empty")
    name = quote(identifier, safe="")
    return name.replace(".", "%2E") if name in (".", "..") else name


__all__ = ["atomic_write_json", "safe_name"]
