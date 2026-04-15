"""JSON document loader — extract text fields from JSON files."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Optional, Union

from raavan.core.rag.loaders.base import BaseDocumentLoader
from raavan.core.rag.vector_store import Document


class JSONLoader(BaseDocumentLoader):
    """Load JSON files by extracting string values.

    For arrays of objects, each object becomes a document.
    For single objects, the entire object becomes one document.
    """

    def __init__(self, text_fields: Optional[list[str]] = None) -> None:
        self.text_fields = text_fields

    async def load(
        self,
        source: Union[str, Path, bytes],
        *,
        metadata: Optional[dict[str, Any]] = None,
    ) -> list[Document]:
        metadata = metadata or {}

        if isinstance(source, bytes):
            raw = source.decode("utf-8", errors="replace")
        else:
            path = Path(source)
            raw = path.read_text(encoding="utf-8", errors="replace")
            metadata.setdefault("source", str(path))

        data = json.loads(raw)
        docs: list[Document] = []

        if isinstance(data, list):
            for i, item in enumerate(data):
                text = self._extract_text(item)
                if text:
                    docs.append(
                        Document(
                            text=text,
                            metadata={**metadata, "item_index": i},
                            id=str(uuid.uuid4()),
                        )
                    )
        else:
            text = self._extract_text(data)
            if text:
                docs.append(
                    Document(
                        text=text,
                        metadata=metadata,
                        id=str(uuid.uuid4()),
                    )
                )

        return docs

    def _extract_text(self, obj: Any) -> str:
        """Extract text from a JSON value."""
        if isinstance(obj, str):
            return obj

        if isinstance(obj, dict):
            if self.text_fields:
                parts = [str(obj[k]) for k in self.text_fields if k in obj]
            else:
                parts = [
                    f"{k}: {v}"
                    for k, v in obj.items()
                    if isinstance(v, (str, int, float))
                ]
            return "\n".join(parts)

        return str(obj)
