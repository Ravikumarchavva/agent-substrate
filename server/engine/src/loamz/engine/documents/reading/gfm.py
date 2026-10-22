"""GitHub-flavoured markdown tables, written once for every format that has them."""

from __future__ import annotations

import re
from collections.abc import Sequence

_WS = re.compile(r"\s+")


def cell(text: object) -> str:
    """One table cell: whitespace collapsed (a newline would end the row), ``|`` escaped."""
    return _WS.sub(" ", "" if text is None else str(text)).strip().replace("|", "\\|")


def gfm_table(rows: Sequence[Sequence[object]], *, header: bool = True) -> str:
    """``rows`` as a GFM table, padded to the widest row. The first row is the header (``header=False`` gives an empty one).

    Returns ``""`` when there is nothing to show."""
    rows = [list(row) for row in rows if row is not None]
    width = max((len(row) for row in rows), default=0)
    if width == 0 or all(not any(cell(c) for c in row) for row in rows):
        return ""
    padded = [[cell(c) for c in row] + [""] * (width - len(row)) for row in rows]
    head, body = (padded[0], padded[1:]) if header else ([""] * width, padded)
    lines = ["| " + " | ".join(head) + " |", "| " + " | ".join(["---"] * width) + " |"]
    lines += ["| " + " | ".join(row) + " |" for row in body]
    return "\n".join(lines)


__all__ = ["cell", "gfm_table"]
