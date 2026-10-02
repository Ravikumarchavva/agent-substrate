"""HTML → markdown. Text, headings, lists, links, tables and code are kept; scripts, styles, forms, frames and anything that can run
or fetch are dropped. Nothing is fetched: images keep only their alt text."""

from __future__ import annotations

import re
from html.parser import HTMLParser

from substrate.documents.reading.gfm import gfm_table

_DROP = {"script", "style", "svg", "iframe", "noscript", "template", "object", "embed", "canvas", "select", "button", "nav", "footer"}
_BLOCK = {"p", "div", "section", "article", "main", "header", "aside", "figure", "figcaption", "address", "dl", "dt", "dd", "details", "summary"}
_VOID = {"br", "hr", "img", "meta", "link", "input", "area", "base", "col", "embed", "source", "track", "wbr"}
_MAX_DEPTH = 100


class _Parser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.blocks: list[str] = []
        self._text: list[str] = []
        self._drop = 0  # nesting depth of the outermost dropped element
        self._drop_tag = ""
        self._lists: list[list[int]] = []  # [counter] for ol, [-1] for ul
        self._href: list[str | None] = []
        self._link_text: list[list[str]] = []
        self._pre = False
        self._in_title = False
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._depth = 0
        self._lead = ""  # the list marker (with its indent) waiting for its item's text

    # -- text ----------------------------------------------------------------------------------------------------------

    def _flush(self, prefix: str = "") -> None:
        text = "".join(self._text)
        self._text = []
        if not self._pre:
            text = re.sub(r"[ \t\r\n\f]+", " ", text)
        text = text.strip()
        lead, self._lead = self._lead, ""
        if text:
            self.blocks.append(lead + prefix + text)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if self._drop:
            if tag == self._drop_tag:
                self._drop += 1
            return
        if tag in _DROP:
            self._drop, self._drop_tag = 1, tag
            return
        self._depth += 1
        if self._depth > _MAX_DEPTH:
            self._drop, self._drop_tag = 1, "\0"  # too deeply nested: the rest is dropped
            return
        if tag == "title":
            self._in_title = True
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._flush()
            self._text.append("#" * int(tag[1]) + " ")
        elif tag in _BLOCK or tag in ("blockquote", "tr", "table"):
            self._flush()
            if tag == "table":
                self._table = []
            elif tag == "tr":
                self._row = []
        elif tag in ("ul", "ol"):
            self._flush()
            self._lists.append([0 if tag == "ol" else -1])
        elif tag == "li":
            self._flush()
            marker = "- "
            if self._lists and self._lists[-1][0] >= 0:
                self._lists[-1][0] += 1
                marker = "1. "
            self._lead = "  " * max(0, len(self._lists) - 1) + marker
        elif tag == "br":
            self._text.append("  \n" if not self._pre else "\n")
        elif tag == "hr":
            self._flush()
            self.blocks.append("---")
        elif tag == "pre":
            self._flush()
            self._pre = True
            self._text.append("```\n")
        elif tag == "code" and not self._pre:
            self._text.append("`")
        elif tag == "a":
            self._href.append(a.get("href"))
            self._link_text.append([])
        elif tag == "img":
            alt = (a.get("alt") or "").strip()
            if alt:
                self._text.append(f"[image: {alt}]")
        elif tag in ("td", "th"):
            self._cell = []
        if tag in _VOID:
            self._depth -= 1

    def handle_endtag(self, tag: str) -> None:
        if self._drop:
            if tag == self._drop_tag:
                self._drop -= 1
            return
        if tag in _VOID:
            return
        self._depth = max(0, self._depth - 1)
        if tag == "title":
            self._in_title = False
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6", "li") or tag in _BLOCK or tag == "blockquote":
            self._flush("> " if tag == "blockquote" else "")
        elif tag in ("ul", "ol"):
            self._flush()
            if self._lists:
                self._lists.pop()
        elif tag == "pre":
            self._text.append("\n```")
            self._flush()
            self._pre = False
        elif tag == "code" and not self._pre:
            self._text.append("`")
        elif tag == "a" and self._href:
            href = self._href.pop()
            label = "".join(self._link_text.pop()).strip()
            if href and label and href.startswith(("http://", "https://", "mailto:")):
                self._replace_last_link(label, f"[{label}]({href})")
        elif tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr":
            if self._table is not None and self._row:
                self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            self._flush()
            table = gfm_table(self._table)
            if table:
                self.blocks.append(table)
            self._table = None

    def handle_data(self, data: str) -> None:
        if self._drop:
            return
        if self._in_title:
            self.title += data
            return
        if self._cell is not None:
            self._cell.append(data)
            return
        if self._link_text:
            self._link_text[-1].append(data)
        self._text.append(data)

    def _replace_last_link(self, label: str, markdown: str) -> None:
        for index in range(len(self._text) - 1, -1, -1):
            if label in self._text[index]:
                self._text[index] = self._text[index].replace(label, markdown, 1)
                return


def html_to_markdown(text: str) -> tuple[str, str | None]:
    """``(markdown, title)`` of an HTML document."""
    parser = _Parser()
    try:
        parser.feed(text)
        parser.close()
    except Exception:  # noqa: BLE001 — html.parser is lenient; whatever it could read stands
        pass
    parser._flush()  # noqa: SLF001
    out: list[str] = []
    for block in parser.blocks:
        is_item = block.lstrip().startswith(("- ", "1. "))
        was_item = bool(out) and out[-1].rsplit("\n", 1)[-1].lstrip().startswith(("- ", "1. "))
        out.append(block) if not (is_item and was_item) else out.__setitem__(-1, out[-1] + "\n" + block)
    markdown = "\n\n".join(out)
    return re.sub(r"\n{3,}", "\n\n", markdown).strip(), (parser.title.strip() or None)


__all__ = ["html_to_markdown"]
