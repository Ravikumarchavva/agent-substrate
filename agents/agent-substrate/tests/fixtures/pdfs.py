"""Builds PDFs for the tests: text at chosen sizes and weights, outlines (bookmarks), and scanned pages (a picture of text, no text layer).

Written with raw PDF syntax so a test states exactly what the file contains; the reader is then held to reading it back."""

from __future__ import annotations

import zlib
from dataclasses import dataclass, field


@dataclass
class Line:
    text: str
    size: float = 12.0
    bold: bool = False
    gap: float = 0.0
    """Extra space above this line, in points (a paragraph break)."""


@dataclass
class Page:
    lines: list[Line] = field(default_factory=list)
    image: tuple[int, int, bytes] | None = None
    """``(width, height, 8-bit gray pixels)`` drawn over the whole page — a scan."""
    figure: tuple[int, int, bytes] | None = None
    """An RGB picture drawn in the middle of the page (``(width, height, RGB bytes)``)."""


def _esc(text: str) -> bytes:
    return (
        text.replace("\\", "\\\\")
        .replace("(", "\\(")
        .replace(")", "\\)")
        .encode("latin-1", "replace")
    )


def build(
    pages: list[Page],
    *,
    outline: list[tuple[str, int, int]] | None = None,
    title: str | None = None,
) -> bytes:
    """A PDF of ``pages``. ``outline`` is ``[(title, level starting at 1, page index)]`` in document order."""
    objects: dict[int, bytes] = {}
    next_id = [3 + 2 * len(pages) + 2]  # after pages, contents, two fonts

    def new_id() -> int:
        value = next_id[0]
        next_id[0] += 1
        return value

    n = len(pages)
    page_ids = [3 + 2 * i for i in range(n)]
    f1, f2 = 3 + 2 * n, 4 + 2 * n
    objects[2] = (
        "<< /Type /Pages /Kids ["
        + " ".join(f"{p} 0 R" for p in page_ids)
        + f"] /Count {n} >>"
    ).encode()
    objects[f1] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    objects[f2] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>"
    for i, page in enumerate(pages):
        resources = f"/Font << /F1 {f1} 0 R /F2 {f2} 0 R >>"
        stream = b""
        xobjects = []
        if page.image:
            w, h, pixels = page.image
            image_id = new_id()
            body = zlib.compress(pixels)
            objects[image_id] = (
                f"<< /Type /XObject /Subtype /Image /Width {w} /Height {h} /ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /FlateDecode /Length {len(body)} >>\nstream\n".encode()
                + body
                + b"\nendstream"
            )
            xobjects.append(f"/Im{image_id} {image_id} 0 R")
            stream += f"q 612 0 0 792 0 0 cm /Im{image_id} Do Q\n".encode()
        if page.figure:
            w, h, pixels = page.figure
            figure_id = new_id()
            body = zlib.compress(pixels)
            objects[figure_id] = (
                f"<< /Type /XObject /Subtype /Image /Width {w} /Height {h} /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode /Length {len(body)} >>\nstream\n".encode()
                + body
                + b"\nendstream"
            )
            xobjects.append(f"/Im{figure_id} {figure_id} 0 R")
            stream += f"q 300 0 0 200 156 300 cm /Im{figure_id} Do Q\n".encode()
        y = 740.0
        for line in page.lines:
            y -= line.gap
            stream += (
                b"BT /"
                + (b"F2" if line.bold else b"F1")
                + f" {line.size:g} Tf 72 {y:.1f} Td (".encode()
                + _esc(line.text)
                + b") Tj ET\n"
            )
            y -= line.size * 1.35
        if xobjects:
            resources += " /XObject << " + " ".join(xobjects) + " >>"
        content_id = 4 + 2 * i
        objects[content_id] = (
            b"<< /Length "
            + str(len(stream)).encode()
            + b" >>\nstream\n"
            + stream
            + b"endstream"
        )
        objects[page_ids[i]] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {content_id} 0 R /Resources << {resources} >> >>".encode()
        )

    catalog = "<< /Type /Catalog /Pages 2 0 R"
    if outline:
        root = new_id()
        ids = [new_id() for _ in outline]
        parents: list[int | None] = []
        stack: list[tuple[int, int]] = []  # (level, index)
        for index, (_t, level, _p) in enumerate(outline):
            while stack and stack[-1][0] >= level:
                stack.pop()
            parents.append(stack[-1][1] if stack else None)
            stack.append((level, index))
        children: dict[int | None, list[int]] = {}
        for index, parent in enumerate(parents):
            children.setdefault(parent, []).append(index)
        for index, (heading, _level, page) in enumerate(outline):
            siblings = children[parents[index]]
            position = siblings.index(index)
            parent_ref = root if parents[index] is None else ids[parents[index]]  # type: ignore[index]
            entry = f"<< /Title ({heading}) /Parent {parent_ref} 0 R /Dest [{page_ids[page]} 0 R /Fit]"
            if position > 0:
                entry += f" /Prev {ids[siblings[position - 1]]} 0 R"
            if position < len(siblings) - 1:
                entry += f" /Next {ids[siblings[position + 1]]} 0 R"
            own = children.get(index)
            if own:
                entry += f" /First {ids[own[0]]} 0 R /Last {ids[own[-1]]} 0 R /Count {len(own)}"
            objects[ids[index]] = (entry + " >>").encode("latin-1")
        top = children[None]
        objects[root] = (
            f"<< /Type /Outlines /First {ids[top[0]]} 0 R /Last {ids[top[-1]]} 0 R /Count {len(top)} >>".encode()
        )
        catalog += f" /Outlines {root} 0 R"
    info = ""
    if title:
        info_id = new_id()
        objects[info_id] = b"<< /Title (" + _esc(title) + b") >>"
        info = f" /Info {info_id} 0 R"
    objects[1] = (catalog + " >>").encode()
    out = b"%PDF-1.4\n"
    offsets = {}
    for ident in sorted(objects):
        offsets[ident] = len(out)
        out += f"{ident} 0 obj\n".encode() + objects[ident] + b"\nendobj\n"
    size = max(objects) + 1
    xref = len(out)
    out += f"xref\n0 {size}\n".encode() + b"0000000000 65535 f \n"
    for ident in range(1, size):
        out += (
            f"{offsets[ident]:010d} 00000 n \n"
            if ident in offsets
            else "0000000000 65535 f \n"
        ).encode()
    out += f"trailer\n<< /Size {size} /Root 1 0 R{info} >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


def scan_of(lines: list[Line], *, dpi: int = 200) -> tuple[int, int, bytes]:
    """The gray pixels of ``lines`` as a page would print them — what a scanner would hand back."""
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(build([Page(lines)]))
    bitmap = document[0].render(scale=dpi / 72, grayscale=True)
    pixels = b"".join(
        bytes(bitmap.buffer)[y * bitmap.stride : y * bitmap.stride + bitmap.width]
        for y in range(bitmap.height)
    )
    width, height = bitmap.width, bitmap.height
    bitmap.close()
    document.close()
    return width, height, pixels


__all__ = ["Line", "Page", "build", "scan_of"]
