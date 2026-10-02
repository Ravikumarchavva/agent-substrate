"""PPTX → markdown pages, one page per slide: its title as ``##``, its text (bullets by level), tables, pictures and speaker notes."""

from __future__ import annotations

from xml.etree import ElementTree as ET

from substrate.documents.reading.build import PageBuilder, image_type
from substrate.documents.reading.docx import A, R, core_title, relationships
from substrate.documents.reading.gfm import gfm_table
from substrate.documents.reading.xmlsafe import MAX_MEDIA_BYTES, Package

P = "http://schemas.openxmlformats.org/presentationml/2006/main"
_TITLES = {"title", "ctrTitle"}
_SKIP = {"ftr", "sldNum", "dt", "hdr"}


def _p(tag: str) -> str:
    return f"{{{P}}}{tag}"


def _a(tag: str) -> str:
    return f"{{{A}}}{tag}"


def _paragraph_text(paragraph: ET.Element) -> str:
    out: list[str] = []
    for node in paragraph:
        if node.tag in (_a("r"), _a("fld")):
            out.append("".join(t.text or "" for t in node.findall(_a("t"))))
        elif node.tag == _a("br"):
            out.append("\n")
    return "".join(out)


class _Slide:
    def __init__(
        self, pkg: Package, part: str, builder: PageBuilder, rels: dict
    ) -> None:
        self.pkg, self.part, self.out, self.rels = pkg, part, builder, rels
        self.title: str | None = None

    def paragraphs(self, body: ET.Element, *, placeholder: str | None) -> list[str]:
        lines: list[str] = []
        for paragraph in body.findall(_a("p")):
            text = (
                " ".join(_paragraph_text(paragraph).split())
                if placeholder in _TITLES
                else _paragraph_text(paragraph).strip()
            )
            if not text:
                continue
            ppr = paragraph.find(_a("pPr"))
            level = int(ppr.get("lvl", 0)) if ppr is not None else 0
            bulleted = ppr is not None and (
                ppr.find(_a("buChar")) is not None
                or ppr.find(_a("buAutoNum")) is not None
            )
            no_bullet = ppr is not None and ppr.find(_a("buNone")) is not None
            in_body = placeholder in ("body", "obj", None) and placeholder is not None
            if bulleted or (in_body and not no_bullet):
                numbered = ppr is not None and ppr.find(_a("buAutoNum")) is not None
                lines.append(
                    "  " * level
                    + ("1. " if numbered else "- ")
                    + text.replace("\n", " ")
                )
            else:
                lines.append(text.replace("\n", "  \n"))
        return lines

    def shape(self, node: ET.Element) -> None:
        tag = node.tag
        if tag == _p("sp"):
            ph = node.find(f"{_p('nvSpPr')}/{_p('nvPr')}/{_p('ph')}")
            kind = ph.get("type", "body") if ph is not None else None
            if kind in _SKIP:
                return
            body = node.find(_p("txBody"))
            if body is None:
                return
            lines = self.paragraphs(body, placeholder=kind)
            if not lines:
                return
            if kind in _TITLES:
                self.title = self.title or " ".join(lines)
                return
            for line in lines:
                self.out.block(line, tight=line.lstrip().startswith(("- ", "1. ")))
        elif tag == _p("pic"):
            blip = node.find(f"{_p('blipFill')}/{_a('blip')}")
            rid = blip.get(f"{{{R}}}embed") if blip is not None else None
            rel = self.rels.get(rid or "")
            alt = (node.find(f"{_p('nvPicPr')}/{_p('cNvPr')}") or ET.Element("x")).get(
                "descr", ""
            )
            if rel is None or rel[2] or image_type(rel[1]) is None:
                self.out.skipped_images += 1
                return
            data = self.pkg.read(rel[1], limit=MAX_MEDIA_BYTES)
            if data:
                self.out.image_block(data, image_type(rel[1]) or "image/png", alt)
        elif tag == _p("graphicFrame"):
            for table in node.iter(_a("tbl")):
                rows = [
                    [
                        " ".join(
                            " ".join(_paragraph_text(p).split())
                            for p in tc.iter(_a("p"))
                        ).strip()
                        for tc in tr.findall(_a("tc"))
                    ]
                    for tr in table.findall(_a("tr"))
                ]
                self.out.block(gfm_table(rows))
        elif tag == _p("grpSp"):
            for child in node:
                self.shape(child)

    def notes(self) -> None:
        for kind, target, _external in self.rels.values():
            if kind != "notesSlide":
                continue
            root = self.pkg.xml(target)
            if root is None:
                return
            texts = []
            for sp in root.iter(_p("sp")):
                ph = sp.find(f"{_p('nvSpPr')}/{_p('nvPr')}/{_p('ph')}")
                if ph is not None and ph.get("type") == "body":
                    body = sp.find(_p("txBody"))
                    if body is not None:
                        texts += [
                            " ".join(_paragraph_text(p).split())
                            for p in body.findall(_a("p"))
                        ]
            note = " ".join(t for t in texts if t)
            if note:
                self.out.block("> Notes: " + note)


def read_pptx(pkg: Package) -> tuple[list, str | None, list[str]]:
    presentation = pkg.xml("ppt/presentation.xml")
    if presentation is None:
        raise ValueError("ppt/presentation.xml is missing")
    rels = relationships(pkg, "ppt/presentation.xml")
    order = [
        rels[sld.get(f"{{{R}}}id", "")][1]
        for sld in presentation.iter(_p("sldId"))
        if sld.get(f"{{{R}}}id", "") in rels
    ]
    builder = PageBuilder()
    first_title: str | None = None
    for number, part in enumerate(order, start=1):
        if number > 1:
            builder.new_page()
        root = pkg.xml(part)
        if root is None:
            continue
        slide = _Slide(pkg, part, builder, relationships(pkg, part))
        tree = root.find(f"{_p('cSld')}/{_p('spTree')}")
        # the slide's own blocks are collected, then the title is put first
        before = len(builder._blocks[-1])  # noqa: SLF001
        if tree is not None:
            for child in tree:
                slide.shape(child)
        heading = f"## {slide.title or f'Slide {number}'}"
        builder._blocks[-1].insert(before, heading)  # noqa: SLF001
        slide.notes()
        first_title = first_title or slide.title
    warnings = (
        [
            f"{builder.skipped_images} picture(s) in unsupported formats were not extracted"
        ]
        if builder.skipped_images
        else []
    )
    return builder.pages(keep_empty=True), core_title(pkg) or first_title, warnings


__all__ = ["read_pptx"]
