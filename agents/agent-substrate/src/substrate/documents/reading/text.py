"""Plain-text formats and the text helpers every reader shares."""

from __future__ import annotations

import csv
import io
import json
import re

from substrate.documents.reading.gfm import gfm_table

MAX_TABLE_ROWS = 1000
MAX_TABLE_COLUMNS = 50

_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+", re.MULTILINE)
_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_MARKER = re.compile(r"<!--.*?-->\s*", re.DOTALL)
_FENCE = re.compile(r"^```.*$", re.MULTILINE)
_RULE = re.compile(r"^\s*\|?\s*(:?-{3,}:?\s*\|?\s*)+$", re.MULTILINE)


def decode_text(data: bytes) -> str:
    """``data`` as text: UTF-8 (with or without a BOM), UTF-16 with a BOM, else Windows-1252 — never raises."""
    if data[:3] == b"\xef\xbb\xbf":
        return data[3:].decode("utf-8", "replace")
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16", "replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252", "replace")


def plain_text(markdown: str) -> str:
    """The text of ``markdown`` without its markup — what search and embeddings should see."""
    text = _MARKER.sub("", markdown)
    text = _IMAGE.sub(lambda m: m.group(1), text)
    text = _LINK.sub(lambda m: m.group(1), text)
    text = _HEADING.sub("", text)
    text = _FENCE.sub("", text)
    text = _RULE.sub("", text)
    text = text.replace("|", " ").replace("<br>", " ")
    return re.sub(r"[ \t]+", " ", re.sub(r"\n{3,}", "\n\n", text)).strip()


def csv_to_markdown(text: str, *, delimiter: str | None = None) -> tuple[str, list[str]]:
    """A CSV/TSV document as a GFM table (sniffing the delimiter), and any truncation warnings."""
    warnings: list[str] = []
    sample = text[:8192]
    if delimiter is None:
        try:
            delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
        except csv.Error:
            delimiter = ","
    rows: list[list[str]] = []
    try:
        for row in csv.reader(io.StringIO(text), delimiter=delimiter):
            rows.append(row[:MAX_TABLE_COLUMNS])
            if len(rows) > MAX_TABLE_ROWS:
                warnings.append(f"table truncated to {MAX_TABLE_ROWS} rows")
                rows = rows[:MAX_TABLE_ROWS]
                break
    except csv.Error as exc:
        warnings.append(f"csv partly unreadable: {exc}")
    if rows and max(len(r) for r in rows) > MAX_TABLE_COLUMNS - 1:
        warnings.append(f"table truncated to {MAX_TABLE_COLUMNS} columns")
    return gfm_table(rows), warnings


def json_to_markdown(text: str) -> str:
    """JSON as a fenced block, pretty-printed when it parses (and kept verbatim when it does not)."""
    try:
        text = json.dumps(json.loads(text), indent=2, ensure_ascii=False)
    except ValueError:
        pass
    return "```json\n" + text + "\n```"


__all__ = ["MAX_TABLE_COLUMNS", "MAX_TABLE_ROWS", "csv_to_markdown", "decode_text", "json_to_markdown", "plain_text"]
