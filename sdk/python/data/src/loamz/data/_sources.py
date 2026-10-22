"""Upload sources: where the bytes of an upload come from, readable by offset and hashable."""

from __future__ import annotations

import hashlib
import os
import tempfile
import threading
from collections.abc import Iterable
from pathlib import Path
from typing import IO, Any

CHUNK = 1024 * 1024
SPOOL_MEMORY = 64 * 1024 * 1024

Source = bytes | bytearray | memoryview | str | os.PathLike | IO[bytes] | Iterable[bytes]


class UploadSource:
    """Knows its size, reads `[offset, offset+length)`, and hashes itself. Close when done."""

    size: int

    def read(self, offset: int, length: int) -> bytes:
        raise NotImplementedError

    def sha256(self) -> str:
        digest = hashlib.sha256()
        for offset in range(0, self.size, CHUNK):
            digest.update(self.read(offset, min(CHUNK, self.size - offset)))
        return digest.hexdigest()

    def close(self) -> None:
        pass


class BytesSource(UploadSource):
    def __init__(self, data: bytes | bytearray | memoryview):
        self.data = memoryview(data).cast("B")
        self.size = len(self.data)

    def read(self, offset: int, length: int) -> bytes:
        return bytes(self.data[offset : offset + length])

    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()


class PathSource(UploadSource):
    """A local file; each read opens its own handle, so parts can be read in parallel."""

    def __init__(self, path: str | os.PathLike):
        self.path = Path(path)
        self.size = self.path.stat().st_size

    def read(self, offset: int, length: int) -> bytes:
        with self.path.open("rb") as stream:
            stream.seek(offset)
            return stream.read(length)


class StreamSource(UploadSource):
    """A binary file object. Seekable ones are read in place; others (and iterables of bytes) are
    first copied to a spooled temporary file, because the size must be known before the upload starts."""

    def __init__(self, stream: Any, *, owned: bool = False):
        self.lock = threading.Lock()
        self.owned = owned
        if not (hasattr(stream, "seekable") and stream.seekable()):
            spool = tempfile.SpooledTemporaryFile(max_size=SPOOL_MEMORY)  # noqa: SIM115
            chunks = iter(lambda: stream.read(CHUNK), b"") if hasattr(stream, "read") else stream
            for chunk in chunks:
                spool.write(chunk)
            if hasattr(stream, "close") and owned:
                stream.close()
            spool.seek(0)
            stream, self.owned = spool, True
        self.stream = stream
        self.origin = stream.tell()
        stream.seek(0, os.SEEK_END)
        self.size = stream.tell() - self.origin

    def read(self, offset: int, length: int) -> bytes:
        with self.lock:
            self.stream.seek(self.origin + offset)
            return self.stream.read(length)

    def close(self) -> None:
        if self.owned:
            self.stream.close()


def open_source(source: Source) -> UploadSource:
    if isinstance(source, bytes | bytearray | memoryview):
        return BytesSource(source)
    if isinstance(source, str | os.PathLike):
        return PathSource(source)
    return StreamSource(source)
