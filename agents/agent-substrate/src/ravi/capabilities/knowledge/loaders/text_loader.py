"""Plain text and Markdown document loader."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Union

from ravi.capabilities.knowledge.loaders.base import BaseDocumentLoader
from ravi.kernel.content import TextBlock
from ravi.kernel.vector import Document


class TextLoader(BaseDocumentLoader):
    """Load ``.txt`` and ``.md`` files as a single document."""

    async def load(
        self,
        source: Union[str, Path, bytes],
        *,
        metadata: dict[str, Any] | None = None,
    ) -> list[Document]:
        metadata = metadata or {}

        if isinstance(source, bytes):
            text = source.decode("utf-8", errors="replace")
        else:
            path = Path(source)
            text = path.read_text(encoding="utf-8", errors="replace")
            metadata.setdefault("source", str(path))

        if not text.strip():
            return []

        return [
            Document(
                content=[TextBlock(text=text)],
                metadata=metadata,
                id=str(uuid.uuid4()),
            )
        ]
