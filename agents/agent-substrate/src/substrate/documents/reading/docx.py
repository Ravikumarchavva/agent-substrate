"""DOCX → markdown pages, from the OOXML parts (``word/document.xml`` and its styles, numbering, relationships, footnotes)."""

from __future__ import annotations

import re
from xml.etree import ElementTree as ET

from substrate.documents.reading.build import PageBuilder, image_type
from substrate.documents.reading.gfm import gfm_table
from substrate.documents.reading.xmlsafe import MAX_MEDIA_BYTES, Package, resolve

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
DC = "http://purl.org/dc/elements/1.1/"

_PAGE = "\u000c"
_IMG = "\u0001"
_HEADING_NAME = re.compile(r"^heading\s*(\d)$", re.IGNORECASE)


def _w(tag: str) -> str:
    return f"{{{W}}}{tag}"


def _val(element: ET.Element | None, tag: str = "val") -> str | None:
    return None if element is None else element.get(_w(tag))


def relationships(pkg: Package, part: str) -> dict[str, tuple[str, str, bool]]:
    """``{id: (type suffix, target path or URL, external)}`` of the relationships of ``part``."""
    directory, _, name = part.rpartition("/")
    root = pkg.xml(f"{directory}/_rels/{name}.rels" if directory else f"_rels/{name}.rels")
    out: dict[str, tuple[str, str, bool]] = {}
    if root is None:
        return out
    for rel in root.findall(f"{{{REL}}}Relationship"):
        external = rel.get("TargetMode") == "External"
        target = rel.get("Target", "")
        out[rel.get("Id", "")] = (rel.get("Type", "").rsplit("/", 1)[-1], target if external else resolve(part, target), external)
    return out


def core_title(pkg: Package) -> str | None:
    root = pkg.xml("docProps/core.xml")
    if root is None:
        return None
    title = root.find(f"{{{DC}}}title")
    return (title.text or "").strip() or None if title is not None else None


class _Styles:
    def __init__(self, pkg: Package) -> None:
        self.level: dict[str, int] = {}
        self.numbered: dict[str, tuple[str, int]] = {}  # styleId -> (numId, ilvl)
        self._based: dict[str, str] = {}
        self.formats: dict[str, dict[int, str]] = {}  # numId -> {ilvl: numFmt}
        styles = pkg.xml("word/styles.xml")
        if styles is not None:
            for style in styles.findall(_w("style")):
                if style.get(_w("type")) != "paragraph":
                    continue
                ident = style.get(_w("styleId"), "")
                name = (_val(style.find(_w("name"))) or "").lower()
                ppr = style.find(_w("pPr"))
                if name == "title":
                    self.level[ident] = 1
                elif (m := _HEADING_NAME.match(name)):
                    self.level[ident] = min(6, int(m.group(1)))
                elif ppr is not None and (o := _val(ppr.find(_w("outlineLvl")))) is not None and o.isdigit() and int(o) < 9:
                    self.level[ident] = min(6, int(o) + 1)
                based = _val(style.find(_w("basedOn")))
                if based:
                    self._based[ident] = based
                if ppr is not None and (num := ppr.find(_w("numPr"))) is not None:
                    self.numbered[ident] = (_val(num.find(_w("numId"))) or "0", int(_val(num.find(_w("ilvl"))) or 0))
        numbering = pkg.xml("word/numbering.xml")
        if numbering is not None:
            abstract: dict[str, dict[int, str]] = {}
            for node in numbering.findall(_w("abstractNum")):
                abstract[node.get(_w("abstractNumId"), "")] = {
                    int(lvl.get(_w("ilvl"), 0)): _val(lvl.find(_w("numFmt"))) or "decimal" for lvl in node.findall(_w("lvl"))
                }
            for num in numbering.findall(_w("num")):
                self.formats[num.get(_w("numId"), "")] = abstract.get(_val(num.find(_w("abstractNumId"))) or "", {})

    def inherited(self, table: dict, style: str | None):
        for _ in range(10):
            if style is None:
                return None
            if style in table:
                return table[style]
            style = self._based.get(style)
        return None


class _Reader:
    def __init__(self, pkg: Package) -> None:
        self.pkg = pkg
        self.styles = _Styles(pkg)
        self.rels = relationships(pkg, "word/document.xml")
        self.out = PageBuilder()
        self.footnotes: dict[str, str] = {}
        self.cited: list[str] = []
        self._load_footnotes()
        self.first_heading: str | None = None

    def _load_footnotes(self) -> None:
        root = self.pkg.xml("word/footnotes.xml")
        if root is None:
            return
        for note in root.findall(_w("footnote")):
            if note.get(_w("type")) in ("separator", "continuationSeparator", "continuationNotice"):
                continue
            text = " ".join(t for p in note.iter(_w("p")) if (t := self.paragraph_text(p).strip()))
            self.footnotes[note.get(_w("id"), "")] = text

    # -- paragraphs -------------------------------------------------------------------------------------------------

    def paragraph_text(self, paragraph: ET.Element) -> str:
        parts: list[str] = []
        self._runs(paragraph, parts)
        return "".join(parts)

    def _runs(self, node: ET.Element, parts: list[str]) -> None:
        for child in node:
            tag = child.tag
            if tag == _w("r"):
                self._run(child, parts)
            elif tag == _w("hyperlink"):
                inner: list[str] = []
                self._runs(child, inner)
                text = "".join(inner)
                rel = self.rels.get(child.get(f"{{{R}}}id", ""))
                parts.append(f"[{text}]({rel[1]})" if rel and rel[2] and text.strip() else text)
            elif tag in (_w("ins"), _w("smartTag"), _w("fldSimple"), _w("sdtContent"), _w("sdt")):
                self._runs(child, parts)
            elif tag == _w("pPr") and child.find(f"{_w('rPr')}/{_w('ins')}") is not None:
                continue

    def _run(self, run: ET.Element, parts: list[str]) -> None:
        for item in run:
            tag = item.tag
            if tag == _w("t"):
                parts.append(item.text or "")
            elif tag == _w("tab"):
                parts.append("\t")
            elif tag in (_w("br"), _w("cr")):
                parts.append(_PAGE if item.get(_w("type")) == "page" else "\n")
            elif tag == _w("lastRenderedPageBreak"):
                parts.append(_PAGE)
            elif tag == _w("noBreakHyphen"):
                parts.append("-")
            elif tag == _w("footnoteReference"):
                ident = item.get(_w("id"), "")
                if ident in self.footnotes:
                    if ident not in self.cited:
                        self.cited.append(ident)
                    parts.append(f"[^{ident}]")
            elif tag in (_w("drawing"), _w("pict"), _w("object")):
                for blip in item.iter(f"{{{A}}}blip"):
                    rid = blip.get(f"{{{R}}}embed") or blip.get(f"{{{R}}}link")
                    alt = ""
                    for doc_pr in item.iter(f"{{{WP}}}docPr"):
                        alt = doc_pr.get("descr") or doc_pr.get("title") or doc_pr.get("name") or ""
                        break
                    if rid:
                        parts.append(f"{_IMG}{rid}\u0002{alt}{_IMG}")
                for imagedata in item.iter("{urn:schemas-microsoft-com:vml}imagedata"):
                    rid = imagedata.get(f"{{{R}}}id")
                    if rid:
                        parts.append(f"{_IMG}{rid}\u0002{_IMG}")

    def _numbering(self, paragraph: ET.Element) -> tuple[str, int] | None:
        ppr = paragraph.find(_w("pPr"))
        num = ppr.find(_w("numPr")) if ppr is not None else None
        style = _val(ppr.find(_w("pStyle"))) if ppr is not None else None
        if num is not None:
            num_id = _val(num.find(_w("numId")))
            ilvl = int(_val(num.find(_w("ilvl"))) or 0)
            if num_id is None:  # numbering by style, with this paragraph supplying only the level
                inherited = self.styles.inherited(self.styles.numbered, style)
                num_id = inherited[0] if inherited else "0"
        else:
            inherited = self.styles.inherited(self.styles.numbered, style)
            if inherited is None:
                return None
            num_id, ilvl = inherited
        return None if num_id in ("0", None) else (num_id, ilvl)

    def _heading_level(self, paragraph: ET.Element) -> int:
        ppr = paragraph.find(_w("pPr"))
        if ppr is None:
            return 0
        outline = _val(ppr.find(_w("outlineLvl")))
        if outline is not None and outline.isdigit():
            return min(6, int(outline) + 1) if int(outline) < 9 else 0
        return self.styles.inherited(self.styles.level, _val(ppr.find(_w("pStyle")))) or 0

    # -- emitting -------------------------------------------------------------------------------------------------------

    def emit_text(self, text: str, *, prefix: str = "") -> None:
        """Append ``text`` to the current page, honouring page breaks and image tokens inside it."""
        first = True
        for piece in text.split(_PAGE):
            if not first:
                self.out.new_page()
            first = False
            self._emit_piece(piece, prefix if piece.strip() else "")

    def _emit_piece(self, piece: str, prefix: str) -> None:
        position = 0
        for match in re.finditer(rf"{_IMG}([^\u0002]*)\u0002([^{_IMG}]*){_IMG}", piece):
            self._emit_plain(piece[position : match.start()], prefix)
            prefix = ""
            self._emit_image(match.group(1), match.group(2))
            position = match.end()
        self._emit_plain(piece[position:], prefix)

    def _emit_plain(self, text: str, prefix: str) -> None:
        text = re.sub(r"[ \t]*\n[ \t]*", "  \n", text.strip(" "))
        if text.strip():
            self.out.block(prefix + text.strip(), tight=bool(prefix))

    def _emit_image(self, rid: str, alt: str) -> None:
        rel = self.rels.get(rid)
        if rel is None or rel[2] or image_type(rel[1]) is None:
            self.out.skipped_images += 1
            return
        data = self.pkg.read(rel[1], limit=MAX_MEDIA_BYTES)
        if data:
            self.out.image_block(data, image_type(rel[1]) or "image/png", alt)

    def paragraph(self, paragraph: ET.Element) -> None:
        text = self.paragraph_text(paragraph)
        ppr = paragraph.find(_w("pPr"))
        if ppr is not None and ppr.find(_w("pageBreakBefore")) is not None and text.strip():
            self.out.new_page()
        level = self._heading_level(paragraph)
        flat = " ".join(text.replace(_PAGE, " ").split())
        if level and flat and _IMG not in text:
            if self.first_heading is None and level == 1:
                self.first_heading = flat
            if _PAGE in text:  # a heading that starts a new page: break first
                self.out.new_page()
            self.out.block("#" * level + " " + flat)
            return
        number = self._numbering(paragraph)
        prefix = ""
        if number is not None:
            kind = self.styles.formats.get(number[0], {}).get(number[1], "bullet")
            prefix = "  " * number[1] + ("- " if kind == "bullet" else "1. ")
        self.emit_text(text, prefix=prefix)

    def table(self, table: ET.Element) -> None:
        rows: list[list[str]] = []
        for tr in table.findall(_w("tr")):
            row: list[str] = []
            for tc in tr.findall(_w("tc")):
                tcpr = tc.find(_w("tcPr"))
                span = int(_val(tcpr.find(_w("gridSpan"))) or 1) if tcpr is not None else 1
                vmerge = tcpr.find(_w("vMerge")) if tcpr is not None else None
                text = "" if (vmerge is not None and _val(vmerge) in (None, "continue")) else " ".join(
                    " ".join(self.paragraph_text(p).replace(_PAGE, " ").split()) for p in tc.iter(_w("p"))
                ).strip()
                text = re.sub(f"{_IMG}[^{_IMG}]*{_IMG}", "", text)
                row.append(text)
                row.extend([""] * (span - 1))
            rows.append(row)
        self.out.block(gfm_table(rows))

    def body(self, node: ET.Element) -> None:
        for child in node:
            if child.tag == _w("p"):
                self.paragraph(child)
            elif child.tag == _w("tbl"):
                self.table(child)
            elif child.tag in (_w("sdt"), _w("sdtContent"), _w("customXml")):
                self.body(child if child.tag != _w("sdt") else (child.find(_w("sdtContent")) or child))


def read_docx(pkg: Package) -> tuple[list, str | None, list[str]]:
    """``(pages, title, warnings)``."""
    root = pkg.xml("word/document.xml")
    if root is None:
        raise ValueError("word/document.xml is missing")
    reader = _Reader(pkg)
    body = root.find(_w("body"))
    if body is not None:
        reader.body(body)
    if reader.cited:
        reader.out.block("\n".join(f"[^{i}]: {reader.footnotes[i]}" for i in reader.cited))
    warnings = [f"{reader.out.skipped_images} image(s) in unsupported formats or beyond the per-page limit were not extracted"] if reader.out.skipped_images else []
    return reader.out.pages(), core_title(pkg) or reader.first_heading, warnings


__all__ = ["read_docx", "relationships", "core_title"]
