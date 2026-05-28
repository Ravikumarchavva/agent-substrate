"""Base document loader ABC and loader registry."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Optional, Union

from ravi.extensions.rag.vector_store import Document


class BaseDocumentLoader(ABC):
    """Abstract base class for document loaders.

    Subclasses implement ``load`` to produce ``Document`` objects from
    various file formats.
    """

    @abstractmethod
    async def load(
        self,
        source: Union[str, Path, bytes],
        *,
        metadata: Optional[dict[str, Any]] = None,
    ) -> list[Document]:
        """Load a source and return a list of documents.

        Args:
            source: File path (str/Path) or raw bytes.
            metadata: Base metadata to attach to every document.

        Returns:
            List of ``Document`` objects.
        """
        ...


class DocumentLoaderRegistry:
    """Registry mapping file extensions to loaders."""

    def __init__(self) -> None:
        self._loaders: dict[str, BaseDocumentLoader] = {}

    def register(self, extension: str, loader: BaseDocumentLoader) -> None:
        """Register a loader for a file extension (e.g. ``".pdf"``)."""
        self._loaders[extension.lower()] = loader

    def get_loader(self, path: str | Path) -> BaseDocumentLoader:
        """Get the registered loader for a file's extension."""
        ext = Path(path).suffix.lower()
        loader = self._loaders.get(ext)
        if loader is None:
            raise ValueError(
                f"No loader registered for extension '{ext}'. "
                f"Available: {list(self._loaders.keys())}"
            )
        return loader

    async def load(
        self,
        source: str | Path,
        *,
        metadata: Optional[dict[str, Any]] = None,
    ) -> list[Document]:
        """Auto-detect loader by extension and load."""
        loader = self.get_loader(source)
        return await loader.load(source, metadata=metadata)
