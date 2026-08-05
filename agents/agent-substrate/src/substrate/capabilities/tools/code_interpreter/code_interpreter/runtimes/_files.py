"""Host-side workspace diffing shared by the local runtimes.

After an execution we report only the files it actually created or modified —
the same ``output_files`` entry shape ``sandbox_response`` already consumes, so
one converter serves every runtime. Used by ``bubblewrap`` and ``inprocess``;
the k8s runtime gets equivalent entries from the in-pod server instead.
"""

from __future__ import annotations

import base64
import mimetypes
import os
from pathlib import Path
from typing import Any

from .base import is_display_artifact

MAX_INLINE_FILE_BYTES = 10 * 1024 * 1024


def snapshot(root: Path) -> dict[str, tuple[int, int]]:
    """(mtime_ns, size) per file, so we can report only what the run changed."""
    snap: dict[str, tuple[int, int]] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in filenames:
            path = os.path.join(dirpath, name)
            try:
                st = os.stat(path)
            except OSError:
                continue
            snap[path] = (st.st_mtime_ns, st.st_size)
    return snap


def collect_changed(
    root: Path, before: dict[str, tuple[int, int]]
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for path, meta in sorted(snapshot(root).items()):
        if before.get(path) == meta:
            continue
        entries.append(file_entry(Path(path), root))
    return entries


def file_entry(path: Path, root: Path) -> dict[str, Any]:
    size = path.stat().st_size
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    entry: dict[str, Any] = {
        "name": path.relative_to(root).as_posix(),
        "path": str(path),
        "size": size,
        "mime_type": mime,
        "type": "file",
        "modified_at": path.stat().st_mtime,
    }
    if size <= MAX_INLINE_FILE_BYTES:
        content_b64 = base64.b64encode(path.read_bytes()).decode("ascii")
        entry["content_base64"] = content_b64
        if is_display_artifact(mime):
            entry["data_uri"] = f"data:{mime};base64,{content_b64}"
    else:
        entry["content_base64"] = None
        entry["too_large"] = True
    return entry


__all__ = ["collect_changed", "file_entry", "snapshot"]
