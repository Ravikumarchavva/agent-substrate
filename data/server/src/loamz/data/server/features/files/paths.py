"""Volume path rules: one normal form, and no way to name anything outside the volume."""

from __future__ import annotations

import unicodedata

from loamz.data.storage.errors import InvalidRequestError

MAX_PATH_BYTES = 1024
MAX_SEGMENT_BYTES = 255
# Final segments that the routes give a meaning (`files/{path}/versions`, `…:download-url`, `…:restore`).
RESERVED_SUFFIXES = (":download-url", ":restore")
RESERVED_LAST_SEGMENTS = ("versions",)


def _invalid(detail: str) -> InvalidRequestError:
    return InvalidRequestError(detail)


def _segments(raw: str, what: str) -> list[str]:
    if not isinstance(raw, str):
        raise _invalid(f"The {what} must be a string")
    if "\x00" in raw or any(ord(c) < 0x20 or ord(c) == 0x7F for c in raw):
        raise _invalid(f"The {what} contains control characters")
    if "\\" in raw:
        raise _invalid(f"The {what} must not contain backslashes")
    text = unicodedata.normalize("NFC", raw)
    if text.startswith("/"):
        raise _invalid(f"The {what} must be relative to the volume")
    parts = []
    for part in text.split("/"):
        if part in {".", ".."}:
            raise _invalid(f"The {what} must stay inside the volume")
        if part:  # repeated slashes collapse
            parts.append(part)
    for part in parts:
        if len(part.encode()) > MAX_SEGMENT_BYTES:
            raise _invalid(f"A segment of the {what} is longer than {MAX_SEGMENT_BYTES} bytes")
    return parts


def normalize_path(raw: str) -> str:
    """A file path in its one normal form: no leading slash, no `.`/`..`, no empty segments."""
    parts = _segments(raw, "path")
    if not parts:
        raise _invalid("The path must not be empty")
    if raw.endswith("/"):
        raise _invalid("A file path must not end with a slash")
    last = parts[-1]
    if last in RESERVED_LAST_SEGMENTS or last.endswith(RESERVED_SUFFIXES):
        raise _invalid(f"The file name {last!r} is reserved")
    path = "/".join(parts)
    if len(path.encode()) > MAX_PATH_BYTES:
        raise _invalid(f"The path is longer than {MAX_PATH_BYTES} bytes")
    return path


def normalize_prefix(raw: str | None) -> str:
    """A listing/deletion prefix: empty (the whole volume) or segments, with a trailing slash kept.

    `docs/rep` matches `docs/report.txt` (a plain string prefix); `docs/` matches only inside `docs`.
    """
    if not raw:
        return ""
    parts = _segments(raw, "prefix")
    prefix = "/".join(parts)
    if prefix and raw.endswith("/"):
        prefix += "/"
    if len(prefix.encode()) > MAX_PATH_BYTES:
        raise _invalid(f"The prefix is longer than {MAX_PATH_BYTES} bytes")
    return prefix


def like_pattern(prefix: str) -> str:
    """A SQL LIKE pattern (ESCAPE '\\') matching every path that starts with `prefix`."""
    return prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def overlaps(a: str, b: str) -> bool:
    return a.startswith(b) or b.startswith(a)
