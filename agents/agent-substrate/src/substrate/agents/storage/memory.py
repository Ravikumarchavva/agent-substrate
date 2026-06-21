"""In-memory file store for local development and testing (L1)."""

from __future__ import annotations


class InMemoryFileStore:
    """Dict-backed file store. No infrastructure required; bytes held in memory."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[bytes, str]] = {}

    async def upload(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str = "application/octet-stream",
    ) -> None:
        self._store[key] = (data, content_type)

    async def download(self, key: str) -> bytes:
        try:
            return self._store[key][0]
        except KeyError:
            raise KeyError(f"Object not found: {key}")

    async def delete(self, key: str) -> None:
        self._store.pop(key, None)

    async def presign_url(self, key: str, *, expires_in: int = 3600) -> str:
        # No real URL — caller detects "memory://" and falls back to /files/{id}/download
        return f"memory://{key}"

    async def connect(self) -> None:
        pass

    async def disconnect(self) -> None:
        pass
