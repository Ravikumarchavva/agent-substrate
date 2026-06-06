"""CSV document loader — one Document per row or per file."""

from __future__ import annotations

import csv
import io
import uuid
from pathlib import Path
from typing import Any, Optional, Union

from ravi.capabilities.knowledge.loaders.base import BaseDocumentLoader
from ravi.kernel.vector import Document


class CSVLoader(BaseDocumentLoader):
    """Load CSV files.

    By default each row becomes a separate ``Document`` with header-value
    pairs as text.  Set ``per_row=False`` to load the entire file as one
    document.
    """

    def __init__(self, per_row: bool = True) -> None:
        self.per_row = per_row

    async def load(
        self,
        source: Union[str, Path, bytes],
        *,
        metadata: Optional[dict[str, Any]] = None,
    ) -> list[Document]:
        metadata = metadata or {}

        if isinstance(source, bytes):
            text = source.decode("utf-8", errors="replace")
        else:
            path = Path(source)
            text = path.read_text(encoding="utf-8", errors="replace")
            metadata.setdefault("source", str(path))

        reader = csv.DictReader(io.StringIO(text))
        docs: list[Document] = []

        if self.per_row:
            for row_idx, row in enumerate(reader):
                row_text = "\n".join(f"{k}: {v}" for k, v in row.items() if v)
                if row_text.strip():
                    docs.append(
                        Document(
                            text=row_text,
                            metadata={**metadata, "row_index": row_idx},
                            id=str(uuid.uuid4()),
                        )
                    )
        else:
            # Whole file as one document
            lines: list[str] = []
            for row in reader:
                line = " | ".join(f"{k}: {v}" for k, v in row.items() if v)
                if line:
                    lines.append(line)
            if lines:
                docs.append(
                    Document(
                        text="\n".join(lines),
                        metadata=metadata,
                        id=str(uuid.uuid4()),
                    )
                )

        return docs
