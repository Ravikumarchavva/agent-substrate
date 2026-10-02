"""The isolated reader process: ``python -s -m substrate.documents.reading.worker``.

It reads frames from stdin — a 4-byte big-endian length, then that many bytes — and answers on a private copy of stdout (so nothing a
parser prints can corrupt the stream). A request is two frames, a JSON header then the document's bytes; the answer is one frame,
the ``ExtractionResult`` as JSON. **Nothing is ever unpickled in either direction.**

Before it touches a document the process caps its own address space, forbids writing files, and disables core dumps, so a PDF that
tries to exhaust memory or fill a disk ends its own process and nothing else. EOF on stdin (the host closing, or dying) ends it.
"""

from __future__ import annotations

import json
import os
import struct
import sys
import time


def read_frame(stream) -> bytes | None:
    header = stream.read(4)
    if len(header) < 4:
        return None
    (size,) = struct.unpack(">I", header)
    body = stream.read(size)
    return body if len(body) == size else None


def write_frame(stream, payload: bytes) -> None:
    stream.write(struct.pack(">I", len(payload)) + payload)
    stream.flush()


def _limit(memory_bytes: int) -> None:
    try:
        import resource
    except ImportError:  # Windows: only the host's wall-clock kill applies
        return
    try:
        with open("/proc/self/statm") as handle:
            baseline = int(handle.read().split()[0]) * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError):
        baseline = 0
    for name, soft in (("RLIMIT_AS", baseline + memory_bytes), ("RLIMIT_FSIZE", 0), ("RLIMIT_CORE", 0)):
        limit = getattr(resource, name, None)
        if limit is None or sys.platform == "darwin" and name == "RLIMIT_AS":  # macOS does not enforce RLIMIT_AS
            continue
        try:
            resource.setrlimit(limit, (soft, soft))
        except (ValueError, OSError):
            pass


def main() -> None:
    inp = sys.stdin.buffer
    out = os.fdopen(os.dup(1), "wb")
    os.dup2(2, 1)  # stray prints go to stderr, never into the frame stream
    from substrate.documents.reading.engine import read_document  # noqa: PLC0415 — heavy imports happen once, before the limits
    from substrate.documents.reading.ocr import resolve_ocr  # noqa: PLC0415
    from substrate.documents.types import ReadLimits  # noqa: PLC0415

    try:
        import pypdfium2  # noqa: F401, PLC0415 — loaded now so the address-space baseline includes it
    except ImportError:
        pass
    ocr_cache: dict[str, object] = {}
    limited = False
    while True:
        frame = read_frame(inp)
        if frame is None:
            return
        header = json.loads(frame)
        data = read_frame(inp)
        if data is None:
            return
        limits = ReadLimits.model_validate(header["limits"])
        spec = header.get("ocr", "none")
        if spec not in ocr_cache:
            try:
                ocr_cache[spec] = resolve_ocr(spec)
                warm = getattr(ocr_cache[spec], "warm", None)
                if warm:
                    warm()  # before the cap: a model runtime maps far more address space than it uses
            except (RuntimeError, ValueError):
                ocr_cache[spec] = None
        if not limited:
            _limit(limits.memory_bytes)
            limited = True
        deadline = time.monotonic() + float(header["timeout_s"])
        result = read_document(
            data,
            header.get("filename", ""),
            header.get("content_type"),
            strategy=header.get("strategy", "auto"),
            limits=limits,
            ocr=ocr_cache[spec],  # type: ignore[arg-type]
            languages=tuple(header.get("languages", ["eng"])),
            deadline=deadline,
        )
        write_frame(out, result.model_dump_json().encode("utf-8"))


if __name__ == "__main__":
    main()
