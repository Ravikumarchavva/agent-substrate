"""Assembling pages: every format reader writes blocks of markdown and images here, and gets ``ExtractedPage``s back."""

from __future__ import annotations

from substrate.documents.reading.text import plain_text
from substrate.documents.types import ExtractedImage, ExtractedImageLabel, ExtractedPage, PageMethod

IMAGE_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    "bmp": "image/bmp",
    "tif": "image/tiff",
    "tiff": "image/tiff",
}
MAX_IMAGES_PER_PAGE = 12


def image_type(name: str) -> str | None:
    """The media type of an embedded image by its file name, or ``None`` for formats a model cannot see (EMF, WMF, SVG…)."""
    return IMAGE_TYPES.get(name.rsplit(".", 1)[-1].lower())


class PageBuilder:
    def __init__(self, *, method: PageMethod = "native") -> None:
        self._method = method
        self._blocks: list[list[str]] = [[]]
        self._images: list[list[ExtractedImage]] = [[]]
        self.skipped_images = 0

    @property
    def number(self) -> int:
        return len(self._blocks)

    def block(self, markdown: str, *, tight: bool = False) -> None:
        """Add a block. ``tight`` joins it to the previous block with one newline instead of a blank line (list items)."""
        if markdown and markdown.strip():
            previous = self._blocks[-1][-1].rsplit("\n", 1)[-1].lstrip() if self._blocks[-1] else ""
            if tight and previous[:2] == markdown.lstrip()[:2] and previous.startswith(("- ", "1. ")):
                self._blocks[-1][-1] += "\n" + markdown.rstrip()
            else:
                self._blocks[-1].append(markdown.rstrip())

    def new_page(self) -> None:
        self._blocks.append([])
        self._images.append([])

    def image(self, data: bytes, media_type: str, alt: str = "", *, label: ExtractedImageLabel = ExtractedImageLabel.FIGURE) -> str | None:
        """Attach an image to the current page and return its ``cid:`` id (``None`` if the page already has too many)."""
        if len(self._images[-1]) >= MAX_IMAGES_PER_PAGE:
            self.skipped_images += 1
            return None
        ident = f"img-p{self.number}-{len(self._images[-1]) + 1}"
        self._images[-1].append(
            ExtractedImage(data=data, media_type=media_type, page_number=self.number, label=label, caption=alt or None, id=ident)
        )
        return ident

    def image_block(self, data: bytes, media_type: str, alt: str = "") -> None:
        ident = self.image(data, media_type, alt)
        if ident:
            self.block(f"![{(alt or 'figure').replace(chr(10), ' ')}](cid:{ident})")

    def pages(self, *, keep_empty: bool = False) -> list[ExtractedPage]:
        """The pages built so far. Empty pages are dropped (a document always keeps at least its first)."""
        out: list[ExtractedPage] = []
        for index, blocks in enumerate(self._blocks):
            markdown = "\n\n".join(blocks)
            if not markdown.strip() and not self._images[index] and not keep_empty and (out or index < len(self._blocks) - 1):
                continue
            out.append(
                ExtractedPage(
                    page_number=len(out) + 1,
                    text=plain_text(markdown),
                    markdown=markdown,
                    images=self._images[index],
                    method=self._method,
                )
            )
        if not out:
            out.append(ExtractedPage(page_number=1, text="", markdown="", method=self._method))
        return _renumber(out)


def _renumber(pages: list[ExtractedPage]) -> list[ExtractedPage]:
    """Dropping an empty page shifts the numbers after it; image ids and ``page_number`` follow."""
    fixed: list[ExtractedPage] = []
    for number, page in enumerate(pages, start=1):
        if page.page_number == number:
            fixed.append(page)
            continue
        old = {img.id: f"img-p{number}-{img.id.rsplit('-', 1)[-1]}" for img in page.images}
        markdown = page.markdown
        for before, after in old.items():
            markdown = markdown.replace(f"cid:{before})", f"cid:{after})")
        fixed.append(
            page.model_copy(
                update={
                    "page_number": number,
                    "markdown": markdown,
                    "images": [img.model_copy(update={"page_number": number, "id": old[img.id]}) for img in page.images],
                }
            )
        )
    return fixed


def join_blocks(blocks: list[str]) -> str:
    """Blocks as markdown: a blank line between them, except consecutive list items of one kind, which stay together."""
    out: list[str] = []
    for block in blocks:
        marker = block.lstrip()[:2]
        previous = out[-1].rsplit("\n", 1)[-1].lstrip()[:2] if out else ""
        if out and marker == previous and marker in ("- ", "1."):
            out[-1] += "\n" + block
        else:
            out.append(block)
    return "\n\n".join(out)


def document_markdown(pages: list[ExtractedPage]) -> str:
    """The whole document: each page after its ``<!-- page N -->`` marker."""
    return "\n\n".join(f"<!-- page {p.page_number} -->\n\n{p.markdown}".rstrip() for p in pages)


__all__ = ["IMAGE_TYPES", "MAX_IMAGES_PER_PAGE", "PageBuilder", "document_markdown", "image_type", "join_blocks"]
