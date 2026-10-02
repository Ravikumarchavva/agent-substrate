"""A pool of isolated reader processes (``worker.py``), called from threads: no event-loop affinity, hard wall-clock kills.

Why a process at all: PDFium is a large C library parsing hostile bytes. A crash, a hang or a memory bomb in it must end a worker, not
the host — and PDFium is not thread-safe, so separate processes are also the only way to read several documents at once. The cost:
about 100–250 ms the first time each worker starts (imports), a millisecond or two per document after, and one process's memory per
worker while it is alive. Workers are reused, recycled after ``RECYCLE_AFTER`` documents, and exit when idle.
"""

from __future__ import annotations

import atexit
import json
import os
import signal
import struct
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field

RECYCLE_AFTER = 100
IDLE_SECONDS = 60.0
KILL_GRACE_S = 2.0


class WorkerFailure(Exception):
    """The worker did not answer: it timed out (and was killed), or it died."""

    def __init__(self, message: str, *, timed_out: bool = False) -> None:
        super().__init__(message)
        self.timed_out = timed_out


@dataclass
class _Worker:
    proc: subprocess.Popen
    jobs: int = 0
    last_used: float = field(default_factory=time.monotonic)

    def kill(self) -> None:
        try:
            if os.name == "posix":
                os.killpg(self.proc.pid, signal.SIGKILL)  # the worker and anything it started (tesseract)
            else:
                self.proc.kill()
        except (ProcessLookupError, PermissionError, OSError):
            pass
        try:
            self.proc.wait(timeout=5)
        except subprocess.SubprocessError:
            pass
        for stream in (self.proc.stdin, self.proc.stdout):
            try:
                if stream:
                    stream.close()
            except OSError:
                pass


def _frame(payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + payload


def _read_exact(stream, size: int) -> bytes | None:
    chunks, remaining = [], size
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            return None
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


class WorkerPool:
    def __init__(self, size: int | None = None, *, command: list[str] | None = None) -> None:
        """``command`` replaces the worker program (another interpreter, or a stub in a test); it must speak the frame protocol."""
        self.size = size or max(1, min(4, (os.cpu_count() or 2)))
        self._command = command or [sys.executable, "-s", "-m", "substrate.documents.reading.worker"]
        self._idle: list[_Worker] = []
        self._count = 0
        self._cond = threading.Condition()
        self._closed = False
        atexit.register(self.close)

    # -- lifecycle ---------------------------------------------------------------------------------------------------

    def _spawn(self) -> _Worker:
        proc = subprocess.Popen(
            self._command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=(os.name == "posix"),
            close_fds=True,
        )
        return _Worker(proc)

    def _acquire(self) -> _Worker:
        with self._cond:
            while True:
                if self._closed:
                    raise WorkerFailure("the reader pool is closed")
                now = time.monotonic()
                keep: list[_Worker] = []
                for worker in self._idle:
                    if worker.proc.poll() is not None or now - worker.last_used > IDLE_SECONDS:
                        worker.kill()
                        self._count -= 1
                    else:
                        keep.append(worker)
                self._idle = keep
                if self._idle:
                    return self._idle.pop()
                if self._count < self.size:
                    self._count += 1
                    try:
                        return self._spawn()
                    except OSError as exc:
                        self._count -= 1
                        raise WorkerFailure(f"could not start a reader process: {exc}") from exc
                self._cond.wait(timeout=1.0)

    def _release(self, worker: _Worker, *, healthy: bool) -> None:
        with self._cond:
            worker.jobs += 1
            worker.last_used = time.monotonic()
            if healthy and worker.proc.poll() is None and worker.jobs < RECYCLE_AFTER and not self._closed:
                self._idle.append(worker)
            else:
                worker.kill()
                self._count -= 1
            self._cond.notify()

    def close(self) -> None:
        with self._cond:
            self._closed = True
            for worker in self._idle:
                worker.kill()
            self._count -= len(self._idle)
            self._idle = []
            self._cond.notify_all()

    # -- one request -------------------------------------------------------------------------------------------------

    def run(self, header: dict, data: bytes, *, timeout_s: float) -> bytes:
        """Send one document to a worker and return its JSON answer. Raises ``WorkerFailure``."""
        worker = self._acquire()
        outcome: dict[str, object] = {}

        def exchange() -> None:
            try:
                stdin, stdout = worker.proc.stdin, worker.proc.stdout
                stdin.write(_frame(json.dumps(header).encode("utf-8")) + _frame(data))  # type: ignore[union-attr]
                stdin.flush()  # type: ignore[union-attr]
                size_bytes = _read_exact(stdout, 4)
                if size_bytes is None:
                    outcome["error"] = "the reader process died while reading this document (out of memory, or a crash in the parser)"
                    return
                payload = _read_exact(stdout, struct.unpack(">I", size_bytes)[0])
                if payload is None:
                    outcome["error"] = "the reader process died while answering"
                    return
                outcome["payload"] = payload
            except (BrokenPipeError, OSError, ValueError) as exc:
                outcome["error"] = f"the reader process failed: {exc}"

        thread = threading.Thread(target=exchange, daemon=True)
        thread.start()
        thread.join(timeout_s + KILL_GRACE_S)
        if thread.is_alive():
            worker.kill()
            thread.join(5)
            self._release(worker, healthy=False)
            raise WorkerFailure(f"reading took longer than {timeout_s:g}s and was stopped", timed_out=True)
        if "payload" not in outcome:
            self._release(worker, healthy=False)
            raise WorkerFailure(str(outcome.get("error", "the reader process failed")))
        self._release(worker, healthy=True)
        return outcome["payload"]  # type: ignore[return-value]


_POOLS: dict[int, WorkerPool] = {}
_POOLS_LOCK = threading.Lock()


def get_pool(size: int | None = None) -> WorkerPool:
    """The process-wide pool (one per requested size)."""
    key = size or 0
    with _POOLS_LOCK:
        pool = _POOLS.get(key)
        if pool is None or pool._closed:  # noqa: SLF001
            pool = _POOLS[key] = WorkerPool(size)
        return pool


__all__ = ["WorkerFailure", "WorkerPool", "get_pool"]
