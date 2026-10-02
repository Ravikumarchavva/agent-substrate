"""XLSX → markdown pages, one page per visible sheet: the cached cell values as a GFM table.

Only what the workbook stores is read — formulas show the value Excel (or LibreOffice) last calculated, never a formula string and
never anything recomputed here. Date-formatted numbers become ISO dates. Output is capped at 1,000 rows × 50 columns per sheet."""

from __future__ import annotations

import datetime as dt
import re
from xml.etree import ElementTree as ET

from substrate.documents.reading.build import PageBuilder
from substrate.documents.reading.docx import R, core_title, relationships
from substrate.documents.reading.gfm import gfm_table
from substrate.documents.reading.text import MAX_TABLE_COLUMNS, MAX_TABLE_ROWS
from substrate.documents.reading.xmlsafe import Package

S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_BUILTIN_DATES = {*range(14, 23), *range(27, 37), *range(45, 48), *range(50, 59)}
_DATE_TOKENS = re.compile(r"[ymdhs]", re.IGNORECASE)
_QUOTED = re.compile(r'"[^"]*"|\[[^\]]*\]|\\.')
_EPOCH = dt.datetime(1899, 12, 30)


def _s(tag: str) -> str:
    return f"{{{S}}}{tag}"


def column_index(reference: str) -> int:
    letters = re.match(r"[A-Za-z]+", reference)
    index = 0
    for ch in (letters.group(0) if letters else "A").upper():
        index = index * 26 + ord(ch) - 64
    return index - 1


def _shared_strings(pkg: Package) -> list[str]:
    root = pkg.xml("xl/sharedStrings.xml")
    if root is None:
        return []
    return [
        "".join(t.text or "" for t in si.iter(_s("t"))) for si in root.findall(_s("si"))
    ]


def _date_styles(pkg: Package) -> set[int]:
    """Indexes of the cell styles (``s=`` on a cell) that format a number as a date or time."""
    root = pkg.xml("xl/styles.xml")
    if root is None:
        return set()
    custom: dict[int, str] = {}
    formats = root.find(_s("numFmts"))
    if formats is not None:
        for fmt in formats.findall(_s("numFmt")):
            custom[int(fmt.get("numFmtId", -1))] = fmt.get("formatCode", "")
    out: set[int] = set()
    xfs = root.find(_s("cellXfs"))
    if xfs is not None:
        for index, xf in enumerate(xfs.findall(_s("xf"))):
            number = int(xf.get("numFmtId", 0))
            if number in _BUILTIN_DATES or (
                number in custom
                and _DATE_TOKENS.search(_QUOTED.sub("", custom[number]))
            ):
                out.add(index)
    return out


def _number(text: str) -> str:
    try:
        value = float(text)
    except ValueError:
        return text
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    return f"{value:.15g}"


def _date(text: str) -> str:
    try:
        serial = float(text)
        moment = _EPOCH + dt.timedelta(days=serial)
    except (ValueError, OverflowError):
        return text
    if serial < 1:
        return moment.strftime("%H:%M")
    return (
        moment.strftime("%Y-%m-%d")
        if serial == int(serial)
        else moment.strftime("%Y-%m-%d %H:%M")
    )


def _cell_value(cell: ET.Element, strings: list[str], dates: set[int]) -> str:
    kind = cell.get("t", "n")
    if kind == "inlineStr":
        return "".join(t.text or "" for t in cell.iter(_s("t")))
    raw = cell.find(_s("v"))
    text = (raw.text or "") if raw is not None else ""
    if not text:
        return ""
    if kind == "s":
        try:
            return strings[int(text)]
        except (ValueError, IndexError):
            return ""
    if kind == "b":
        return "TRUE" if text == "1" else "FALSE"
    if kind in ("str", "e"):
        return text
    if int(cell.get("s", 0) or 0) in dates:
        return _date(text)
    return _number(text)


def _sheet_rows(
    root: ET.Element,
    strings: list[str],
    dates: set[int],
    warnings: list[str],
    name: str,
) -> list[list[str]]:
    rows: dict[int, dict[int, str]] = {}
    truncated_rows = truncated_cols = False
    data = root.find(_s("sheetData"))
    if data is None:
        return []
    for position, row in enumerate(data.findall(_s("row"))):
        number = int(row.get("r", position + 1)) - 1
        if number >= MAX_TABLE_ROWS + 1:
            truncated_rows = True
            break
        cells: dict[int, str] = {}
        for column, cell in enumerate(row.findall(_s("c"))):
            index = column_index(cell.get("r", "")) if cell.get("r") else column
            if index >= MAX_TABLE_COLUMNS:
                truncated_cols = True
                continue
            value = _cell_value(cell, strings, dates)
            if value != "":
                cells[index] = value
        if cells:
            rows[number] = cells
    if truncated_rows:
        warnings.append(f"sheet {name!r} truncated to {MAX_TABLE_ROWS} rows")
    if truncated_cols:
        warnings.append(f"sheet {name!r} truncated to {MAX_TABLE_COLUMNS} columns")
    if not rows:
        return []
    width = max(max(c) for c in rows.values()) + 1
    return [[rows[r].get(c, "") for c in range(width)] for r in sorted(rows)]


def read_xlsx(pkg: Package) -> tuple[list, str | None, list[str]]:
    workbook = pkg.xml("xl/workbook.xml")
    if workbook is None:
        raise ValueError("xl/workbook.xml is missing")
    rels = relationships(pkg, "xl/workbook.xml")
    strings, dates = _shared_strings(pkg), _date_styles(pkg)
    builder, warnings = PageBuilder(), []
    sheets = workbook.find(_s("sheets"))
    first = True
    for sheet in sheets.findall(_s("sheet")) if sheets is not None else []:
        name = sheet.get("name", "Sheet")
        if sheet.get("state", "visible") != "visible":
            warnings.append(f"hidden sheet {name!r} was skipped")
            continue
        rel = rels.get(sheet.get(f"{{{R}}}id", ""))
        root = pkg.xml(rel[1]) if rel else None
        if root is None:
            continue
        if not first:
            builder.new_page()
        first = False
        builder.block(f"## {name}")
        table = gfm_table(_sheet_rows(root, strings, dates, warnings, name))
        builder.block(table or "_(empty sheet)_")
    return builder.pages(keep_empty=True), core_title(pkg), warnings


__all__ = ["column_index", "read_xlsx"]
