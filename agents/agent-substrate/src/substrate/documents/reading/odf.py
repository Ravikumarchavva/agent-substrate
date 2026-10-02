"""OpenDocument (ODT, ODP, ODS) → markdown pages, from ``content.xml``."""

from __future__ import annotations

from xml.etree import ElementTree as ET

from substrate.documents.reading.build import PageBuilder, image_type
from substrate.documents.reading.gfm import gfm_table
from substrate.documents.reading.text import MAX_TABLE_COLUMNS, MAX_TABLE_ROWS
from substrate.documents.reading.xmlsafe import MAX_MEDIA_BYTES, Package

NS = {
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
    "draw": "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0",
    "xlink": "http://www.w3.org/1999/xlink",
    "style": "urn:oasis:names:tc:opendocument:xmlns:style:1.0",
    "fo": "urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0",
    "presentation": "urn:oasis:names:tc:opendocument:xmlns:presentation:1.0",
    "dc": "http://purl.org/dc/elements/1.1/",
    "meta": "urn:oasis:names:tc:opendocument:xmlns:meta:1.0",
}
_REPEAT_CAP = 50


def _q(prefix: str, local: str) -> str:
    return f"{{{NS[prefix]}}}{local}"


def _inline(node: ET.Element, links: bool = True) -> str:
    """The text of a ``text:p`` / ``text:h``, with spans, spaces, tabs, line breaks and links resolved."""
    parts: list[str] = [node.text or ""]
    for child in node:
        tag = child.tag
        if tag == _q("text", "s"):
            parts.append(" " * min(int(child.get(_q("text", "c"), 1)), 40))
        elif tag == _q("text", "tab"):
            parts.append("\t")
        elif tag == _q("text", "line-break"):
            parts.append("\n")
        elif tag == _q("text", "a"):
            text = _inline(child)
            href = child.get(_q("xlink", "href"), "")
            parts.append(f"[{text}]({href})" if links and href.startswith(("http://", "https://", "mailto:")) and text.strip() else text)
        elif tag in (_q("text", "span"), _q("text", "bookmark-ref"), _q("text", "meta"), _q("text", "ruby")):
            parts.append(_inline(child, links))
        elif tag == _q("text", "note"):
            continue  # footnotes are collected by the document reader
        parts.append(child.tail or "")
    return "".join(parts)


class _Content:
    def __init__(self, pkg: Package, root: ET.Element) -> None:
        self.pkg, self.root = pkg, root
        self.out = PageBuilder()
        self.break_styles: set[str] = set()
        self.numbered_lists: set[str] = set()
        self.hidden_tables: set[str] = set()
        styles_xml = pkg.xml("styles.xml")
        for container in (root.find(_q("office", "automatic-styles")), styles_xml.find(_q("office", "styles")) if styles_xml is not None else None,
                          styles_xml.find(_q("office", "automatic-styles")) if styles_xml is not None else None):
            if container is None:
                continue
            for style in container.findall(_q("style", "style")):
                props = style.find(_q("style", "paragraph-properties"))
                if props is not None and props.get(_q("fo", "break-before")) == "page":
                    self.break_styles.add(style.get(_q("style", "name"), ""))
                table_props = style.find(_q("style", "table-properties"))
                if table_props is not None and table_props.get(_q("table", "display")) == "false":
                    self.hidden_tables.add(style.get(_q("style", "name"), ""))
            for lst in container.findall(_q("text", "list-style")):
                first = next(iter(lst), None)
                if first is not None and first.tag == _q("text", "list-level-style-number"):
                    self.numbered_lists.add(lst.get(_q("style", "name"), ""))
        self.footnotes: list[tuple[str, str]] = []
        self.first_heading: str | None = None

    # -- text ---------------------------------------------------------------------------------------------------------

    def _image(self, frame: ET.Element) -> None:
        for image in frame.iter(_q("draw", "image")):
            href = image.get(_q("xlink", "href"), "")
            if href.lower().endswith((".svm", ".emf", ".wmf", ".svg")):
                continue  # a vector replacement/preview of an object, not a picture a model can be shown
            if not href or href.startswith(("http:", "https:", "/", "..")) or image_type(href) is None:
                self.out.skipped_images += 1
                continue
            data = self.pkg.read(href.lstrip("./"), limit=MAX_MEDIA_BYTES)
            alt = ""
            for tag in ("desc", "title"):
                node = frame.find(_q("svg", tag)) if "svg" in NS else None
                if node is not None and node.text:
                    alt = node.text
                    break
            if data:
                self.out.image_block(data, image_type(href) or "image/png", alt)

    def _footnotes(self, node: ET.Element) -> str:
        marks = []
        for note in node.iter(_q("text", "note")):
            body = note.find(_q("text", "note-body"))
            ident = str(len(self.footnotes) + 1)
            text = " ".join(" ".join(_inline(p).split()) for p in body.findall(_q("text", "p"))) if body is not None else ""
            self.footnotes.append((ident, text))
            marks.append(f"[^{ident}]")
        return "".join(marks)

    def paragraph(self, node: ET.Element, *, prefix: str = "") -> None:
        style = node.get(_q("text", "style-name"), "")
        if style in self.break_styles and any(self.out._blocks[-1]):  # noqa: SLF001
            self.out.new_page()
        text = _inline(node).strip().replace("\t", " ")
        text += self._footnotes(node)
        for frame in node.iter(_q("draw", "frame")):
            self._image(frame)
        if not text:
            return
        if node.tag == _q("text", "h") or style == "Title":
            level = 1 if style == "Title" else min(6, int(node.get(_q("text", "outline-level"), 1) or 1))
            flat = " ".join(text.split())
            self.first_heading = self.first_heading or (flat if level == 1 else None)
            self.out.block("#" * level + " " + flat)
        else:
            self.out.block(prefix + text.replace("\n", "  \n"), tight=bool(prefix))

    def list(self, node: ET.Element, depth: int = 0) -> None:
        numbered = node.get(_q("text", "style-name"), "") in self.numbered_lists
        for item in node.findall(_q("text", "list-item")) + node.findall(_q("text", "list-header")):
            for child in item:
                if child.tag in (_q("text", "p"), _q("text", "h")):
                    self.paragraph(child, prefix="  " * depth + ("1. " if numbered else "- "))
                elif child.tag == _q("text", "list"):
                    self.list(child, depth + 1)

    def table(self, node: ET.Element) -> list[list[str]]:
        rows: list[list[str]] = []
        for row in node.iter(_q("table", "table-row")):
            repeat = min(int(row.get(_q("table", "number-rows-repeated"), 1)), 100)
            cells: list[str] = []
            for cell in row:
                if cell.tag not in (_q("table", "table-cell"), _q("table", "covered-table-cell")):
                    continue
                span = int(cell.get(_q("table", "number-columns-repeated"), 1))
                text = " ".join(" ".join(_inline(p).split()) for p in cell.iter(_q("text", "p"))).strip()
                if not text and span > _REPEAT_CAP:
                    continue
                cells.extend([text] * min(span, _REPEAT_CAP))
            cells = cells[:MAX_TABLE_COLUMNS]
            if any(cells):
                rows.extend([cells] * (repeat if repeat > 1 else 1))
            if len(rows) > MAX_TABLE_ROWS:
                rows = rows[: MAX_TABLE_ROWS + 1]
                break
        return rows

    def body(self, node: ET.Element) -> None:
        for child in node:
            tag = child.tag
            if tag in (_q("text", "p"), _q("text", "h")):
                self.paragraph(child)
            elif tag == _q("text", "list"):
                self.list(child)
            elif tag == _q("table", "table"):
                self.out.block(gfm_table(self.table(child)))
            elif tag in (_q("text", "section"), _q("text", "tracked-changes"), _q("text", "table-of-content")):
                if tag != _q("text", "tracked-changes"):
                    self.body(child)
            elif tag == _q("draw", "frame"):
                self._image(child)


def _meta_title(pkg: Package) -> str | None:
    root = pkg.xml("meta.xml")
    node = root.find(f".//{_q('dc', 'title')}") if root is not None else None
    return ((node.text or "").strip() or None) if node is not None else None


def read_odf(pkg: Package, kind: str) -> tuple[list, str | None, list[str]]:
    """``kind`` is ``odt``, ``odp`` or ``ods``."""
    root = pkg.xml("content.xml")
    if root is None:
        raise ValueError("content.xml is missing")
    content = _Content(pkg, root)
    office_body = root.find(_q("office", "body"))
    warnings: list[str] = []
    first_slide: str | None = None
    if office_body is not None:
        if kind == "odt":
            text = office_body.find(_q("office", "text"))
            if text is not None:
                content.body(text)
        elif kind == "odp":
            presentation = office_body.find(_q("office", "presentation"))
            slides = presentation.findall(_q("draw", "page")) if presentation is not None else []
            for number, page in enumerate(slides, start=1):
                if number > 1:
                    content.out.new_page()
                title: str | None = None
                before = len(content.out._blocks[-1])  # noqa: SLF001
                for frame in page.findall(_q("draw", "frame")):
                    box = frame.find(_q("draw", "text-box"))
                    cls = frame.get(_q("presentation", "class"), "")
                    if frame.find(_q("table", "table")) is not None:
                        content.out.block(gfm_table(content.table(frame.find(_q("table", "table")))))  # type: ignore[arg-type]
                    elif box is not None and cls == "title":
                        title = " ".join(" ".join(_inline(p).split()) for p in box.iter(_q("text", "p"))).strip()
                    elif box is not None and cls not in ("footer", "page-number", "date-time", "header"):
                        content.body(box)
                    elif frame.find(_q("draw", "image")) is not None:
                        content._image(frame)  # noqa: SLF001
                content.out._blocks[-1].insert(before, f"## {title or f'Slide {number}'}")  # noqa: SLF001
                first_slide = first_slide or title
                notes = page.find(_q("presentation", "notes"))
                if notes is not None:
                    note = " ".join(" ".join(_inline(p).split()) for p in notes.iter(_q("text", "p"))).strip()
                    if note:
                        content.out.block("> Notes: " + note)
        else:
            sheets = office_body.find(_q("office", "spreadsheet"))
            first = True
            for table in (sheets.findall(_q("table", "table")) if sheets is not None else []):
                name = table.get(_q("table", "name"), "Sheet")
                if table.get(_q("table", "style-name"), "") in content.hidden_tables:
                    warnings.append(f"hidden sheet {name!r} was skipped")
                    continue
                if not first:
                    content.out.new_page()
                first = False
                content.out.block(f"## {name}")
                rows = content.table(table)
                if len(rows) > MAX_TABLE_ROWS:
                    warnings.append(f"sheet {name!r} truncated to {MAX_TABLE_ROWS} rows")
                    rows = rows[:MAX_TABLE_ROWS]
                content.out.block(gfm_table(rows) or "_(empty sheet)_")
    if content.footnotes:
        content.out.block("\n".join(f"[^{i}]: {t}" for i, t in content.footnotes))
    if content.out.skipped_images:
        warnings.append(f"{content.out.skipped_images} image(s) could not be extracted")
    return content.out.pages(keep_empty=True), _meta_title(pkg) or content.first_heading or first_slide, warnings


__all__ = ["read_odf"]
