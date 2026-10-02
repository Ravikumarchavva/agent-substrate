"""What a file is: decided from its bytes first, then the declared type, then the filename.

A declared content type (``application/octet-stream``, a browser's guess) or an extension is only a hint; the bytes win. That is
what makes a ``.docx`` that arrives as ``octet-stream``, or a ``.webp`` a browser labelled ``image/webp``, read like any other.
"""

from __future__ import annotations

import io
import re
import zipfile
from enum import StrEnum

from substrate.documents.reading.text import decode_text


class Format(StrEnum):
    PDF = "pdf"
    DOCX = "docx"
    PPTX = "pptx"
    XLSX = "xlsx"
    ODT = "odt"
    ODP = "odp"
    ODS = "ods"
    HTML = "html"
    MARKDOWN = "markdown"
    TEXT = "text"
    CSV = "csv"
    TSV = "tsv"
    JSON = "json"
    IMAGE = "image"
    OLE = "ole"
    """A legacy binary Office file (``.doc``, ``.ppt``, ``.xls``) — a document server (LibreOffice) reads these, this reader does not."""
    OTHER_ARCHIVE = "archive"
    UNKNOWN = "unknown"


_IMAGE_MAGIC = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a", b"BM", b"II*\x00", b"MM\x00*")
_ODF_TYPES = {
    "application/vnd.oasis.opendocument.text": Format.ODT,
    "application/vnd.oasis.opendocument.presentation": Format.ODP,
    "application/vnd.oasis.opendocument.spreadsheet": Format.ODS,
}
_BY_DECLARED = {
    "application/pdf": Format.PDF,
    "text/html": Format.HTML,
    "application/xhtml+xml": Format.HTML,
    "text/markdown": Format.MARKDOWN,
    "text/x-markdown": Format.MARKDOWN,
    "text/csv": Format.CSV,
    "text/tab-separated-values": Format.TSV,
    "application/json": Format.JSON,
    "text/plain": Format.TEXT,
}
_BY_EXTENSION = {
    ".pdf": Format.PDF,
    ".html": Format.HTML,
    ".htm": Format.HTML,
    ".xhtml": Format.HTML,
    ".md": Format.MARKDOWN,
    ".markdown": Format.MARKDOWN,
    ".csv": Format.CSV,
    ".tsv": Format.TSV,
    ".json": Format.JSON,
    ".txt": Format.TEXT,
    ".text": Format.TEXT,
    ".log": Format.TEXT,
}
_HTML_START = re.compile(
    rb"^\s*(<!doctype\s+html|<html|<head|<body|<\?xml[^>]*>\s*<html|<(p|div|span|h[1-6]|table|ul|ol|a|section|article|br)[\s>/])", re.IGNORECASE
)


def _is_webp(data: bytes) -> bool:
    return data[:4] == b"RIFF" and data[8:12] == b"WEBP"


def _zip_format(data: bytes) -> Format:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = set(archive.namelist())
            if "mimetype" in names:
                declared = archive.read("mimetype")[:128].decode("ascii", "replace").strip()
                if declared in _ODF_TYPES:
                    return _ODF_TYPES[declared]
                return Format.OTHER_ARCHIVE
            if "[Content_Types].xml" in names:
                if "word/document.xml" in names:
                    return Format.DOCX
                if "ppt/presentation.xml" in names:
                    return Format.PPTX
                if "xl/workbook.xml" in names:
                    return Format.XLSX
            return Format.OTHER_ARCHIVE
    except (zipfile.BadZipFile, OSError, ValueError, NotImplementedError):
        return Format.UNKNOWN


def _looks_textual(data: bytes) -> bool:
    head = data[:8192]
    if not head:
        return True
    if head[:2] in (b"\xff\xfe", b"\xfe\xff") or head[:3] == b"\xef\xbb\xbf":
        return True
    if b"\x00" in head:
        return False
    sample = head.decode("utf-8", "ignore")
    printable = sum(1 for ch in sample if ch.isprintable() or ch in "\n\r\t\f")
    return printable / max(1, len(sample)) > 0.95


def sniff(data: bytes, filename: str = "", content_type: str | None = None) -> Format:
    """The format of ``data``. Bytes first, then ``content_type``, then ``filename``'s extension."""
    head = data[:1024]
    if b"%PDF-" in head:
        return Format.PDF
    if head[:4] == b"PK\x03\x04":
        return _zip_format(data)
    if head[:4] == b"\xd0\xcf\x11\xe0":
        return Format.OLE
    if any(head.startswith(m) for m in _IMAGE_MAGIC) or _is_webp(head):
        return Format.IMAGE
    declared = (content_type or "").split(";")[0].strip().lower()
    extension = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename.rsplit("/", 1)[-1] else ""
    if not _looks_textual(data):
        return Format.UNKNOWN
    if _HTML_START.match(decode_text(head).encode("utf-8")) or (declared in ("text/html", "application/xhtml+xml") and "<" in decode_text(head)):
        return Format.HTML
    if declared in _BY_DECLARED and declared != "text/plain":
        return _BY_DECLARED[declared]
    if extension in _BY_EXTENSION:
        return _BY_EXTENSION[extension]
    if declared.startswith("text/") or declared == "text/plain":
        return Format.TEXT
    return Format.TEXT


__all__ = ["Format", "sniff"]
