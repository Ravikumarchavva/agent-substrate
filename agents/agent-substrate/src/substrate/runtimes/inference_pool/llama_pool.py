"""``LocalLlamaServerPool`` / ``RemoteInferencePool`` — the two
:class:`~substrate.runtimes.inference_pool.pool_types.InferencePool`
implementations any consumer (``document_intelligence/service/engines/
paddle_vl.py``, ``embedding_reranker``'s local mode) dispatches inference
through.

``LocalLlamaServerPool`` spawns and supervises N real ``llama-server``
subprocess children (one per GPU, or one CPU child) via
``asyncio.create_subprocess_exec`` — not ``subprocess.Popen``/``run``, which
would block the event loop on ``wait()`` and stream reads.
``RemoteInferencePool`` wraps a list of pre-configured, already-reachable
:class:`InferenceEndpoint`\\ s (a remote sglang/vLLM/other deployment) — no
subprocess involved at all.

See the "``llama_pool.py`` — multi-GPU in one process" section of the Phase 1
plan for the full rationale (least-loaded queue dispatch over round-robin,
partial-success readiness, ``PR_SET_PDEATHSIG`` for orphan prevention).
"""

from __future__ import annotations

import asyncio
import ctypes
import os
import signal
from dataclasses import dataclass, field

import httpx2 as httpx

from substrate.integrations.llm.endpoint import InferenceEndpoint
from substrate.logger import setup_logging
from substrate.runtimes.inference_pool.pool_types import PoolWorker

logger = setup_logging()

_HEALTH_POLL_INTERVAL_S = 1.0
_RESTART_BACKOFF_INITIAL_S = 1.0
_RESTART_BACKOFF_CAP_S = 30.0
_SHUTDOWN_WAIT_TIMEOUT_S = 10.0


def _set_pdeathsig() -> None:
    """Ask the kernel to SIGTERM this child if its parent dies first — real,
    concrete reason: a hard-killed or ``--reload``-restarted parent process
    otherwise orphans GPU-holding ``llama-server`` children that block the
    *next* startup on VRAM. Linux-only; a no-op anywhere else, or if
    ``libc``/``prctl`` can't be reached — this is a safety net, not
    something that should block the pool from working at all."""
    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        PR_SET_PDEATHSIG = 1
        libc.prctl(PR_SET_PDEATHSIG, signal.SIGTERM)
    except (OSError, AttributeError) as exc:
        logger.warning("PR_SET_PDEATHSIG unavailable, skipping: %s", exc)


def _parse_gpu_index(device: str) -> str | None:
    """``"gpu:2"`` -> ``"2"``; ``"cpu"`` -> ``None`` (don't set
    ``CUDA_VISIBLE_DEVICES`` at all for a CPU worker)."""
    if device.startswith("gpu:"):
        return device.split(":", 1)[1]
    return None


@dataclass
class _LocalWorkerState:
    """Internal bookkeeping for one spawned child — not part of the public
    ``PoolWorker`` contract, which only carries what ``paddle_vl.py`` needs."""

    index: int
    port: int
    gpu_device: str
    proc: asyncio.subprocess.Process | None = None
    drain_tasks: list[asyncio.Task] = field(default_factory=list)
    supervisor_task: asyncio.Task | None = None
    stopping_intentionally: bool = False
    dead: bool = False


class LocalLlamaServerPool:
    """Spawns and supervises N ``llama-server`` subprocess children, one per
    entry in ``gpu_devices`` (each ``"gpu:N"`` or, for CPU mode, a single
    ``"cpu"`` entry). Implements :class:`InferencePool`."""

    def __init__(
        self,
        *,
        binary: str,
        main_gguf: str,
        mmproj_gguf: str,
        gpu_devices: list[str],
        base_port: int = 8090,
        ngl: int = 99,
        slots: int = 1,
        ctx_size: int = 6144,
        threads: int | None = None,
        startup_timeout_s: float = 300.0,
        max_restarts: int = 5,
        model_name: str = "compatible/PaddleOCR-VL-1.6",
    ) -> None:
        self._binary = binary
        self._main_gguf = main_gguf
        self._mmproj_gguf = mmproj_gguf
        self._gpu_devices = gpu_devices
        self._base_port = base_port
        self._ngl = ngl
        self._slots = slots
        self._ctx_size = ctx_size
        self._threads = threads
        self._startup_timeout_s = startup_timeout_s
        self._max_restarts = max_restarts
        self._model_name = model_name

        self.ready = False
        self.worker_count = 0

        self._states: dict[int, _LocalWorkerState] = {}
        self._queue: asyncio.Queue[PoolWorker] = asyncio.Queue()
        self._closed = False
        self._close_lock = asyncio.Lock()

    def _argv(self, port: int) -> list[str]:
        argv = [
            self._binary,
            "-m",
            self._main_gguf,
            "--mmproj",
            self._mmproj_gguf,
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "-ngl",
            str(self._ngl),
            "-np",
            str(self._slots),
            "-cb",
            "--ctx-size",
            str(self._ctx_size),
            "--temp",
            "0",
            "--seed",
            "0",
        ]
        if self._threads is not None:
            argv += ["--threads", str(self._threads)]
        return argv

    async def _drain_stream(self, stream: asyncio.StreamReader, index: int, tag: str) -> None:
        try:
            while True:
                line = await stream.readline()
                if not line:
                    break
                logger.debug("vl-worker-%d[%s]: %s", index, tag, line.decode(errors="replace").rstrip())
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - draining must never crash the pool
            logger.warning("vl-worker-%d[%s] drain error: %s", index, tag, exc)

    async def _spawn(self, index: int, device: str) -> asyncio.subprocess.Process:
        port = self._base_port + index
        env = dict(os.environ)
        gpu_index = _parse_gpu_index(device)
        if gpu_index is not None:
            env["CUDA_VISIBLE_DEVICES"] = gpu_index

        kwargs: dict = {}
        if hasattr(os, "fork"):  # POSIX only - preexec_fn is invalid on Windows
            kwargs["preexec_fn"] = _set_pdeathsig

        proc = await asyncio.create_subprocess_exec(
            *self._argv(port),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **kwargs,
        )
        return proc

    async def _wait_healthy(self, port: int) -> bool:
        deadline = asyncio.get_event_loop().time() + self._startup_timeout_s
        url = f"http://127.0.0.1:{port}/health"
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
            while asyncio.get_event_loop().time() < deadline:
                try:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        return True
                except httpx.RequestError:
                    pass
                await asyncio.sleep(_HEALTH_POLL_INTERVAL_S)
        return False

    async def _start_worker(self, index: int, device: str) -> None:
        state = _LocalWorkerState(index=index, port=self._base_port + index, gpu_device=device)
        self._states[index] = state

        proc = await self._spawn(index, device)
        state.proc = proc
        assert proc.stdout is not None and proc.stderr is not None
        state.drain_tasks = [
            asyncio.create_task(self._drain_stream(proc.stdout, index, "stdout")),
            asyncio.create_task(self._drain_stream(proc.stderr, index, "stderr")),
        ]

        healthy = await self._wait_healthy(state.port)
        if not healthy:
            state.stopping_intentionally = True
            await self._terminate_proc(proc)
            state.dead = True
            raise RuntimeError(f"vl-worker-{index} on device {device} never became healthy")

        state.supervisor_task = asyncio.create_task(self._supervise(state))
        self._queue.put_nowait(self._make_pool_worker(state))

    def _make_pool_worker(self, state: _LocalWorkerState) -> PoolWorker:
        return PoolWorker(
            index=state.index,
            endpoint=InferenceEndpoint(
                model=self._model_name,
                base_url=f"http://127.0.0.1:{state.port}",
            ),
            layout_device=state.gpu_device,
        )

    async def _supervise(self, state: _LocalWorkerState) -> None:
        """Watches a spawned child; on an unexpected exit, restarts it with
        exponential backoff up to ``max_restarts`` attempts. Permanently
        dead after that — never re-queued, ``worker_count`` decremented."""
        backoff = _RESTART_BACKOFF_INITIAL_S
        restarts = 0
        while True:
            assert state.proc is not None
            await state.proc.wait()
            if state.stopping_intentionally or self._closed:
                return

            logger.warning(
                "vl-worker-%d exited unexpectedly (code=%s)", state.index, state.proc.returncode
            )
            for task in state.drain_tasks:
                task.cancel()

            if restarts >= self._max_restarts:
                logger.error(
                    "vl-worker-%d exceeded max_restarts=%d, marking permanently dead",
                    state.index,
                    self._max_restarts,
                )
                state.dead = True
                self.worker_count = max(0, self.worker_count - 1)
                if self.worker_count == 0:
                    self.ready = False
                return

            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _RESTART_BACKOFF_CAP_S)
            restarts += 1
            try:
                proc = await self._spawn(state.index, state.gpu_device)
                state.proc = proc
                assert proc.stdout is not None and proc.stderr is not None
                state.drain_tasks = [
                    asyncio.create_task(self._drain_stream(proc.stdout, state.index, "stdout")),
                    asyncio.create_task(self._drain_stream(proc.stderr, state.index, "stderr")),
                ]
                healthy = await self._wait_healthy(state.port)
                if not healthy:
                    logger.warning("vl-worker-%d restart did not become healthy, retrying", state.index)
                    state.stopping_intentionally = True
                    await self._terminate_proc(proc)
                    state.stopping_intentionally = False
                    continue
                logger.info("vl-worker-%d restarted successfully (attempt %d)", state.index, restarts)
                self._queue.put_nowait(self._make_pool_worker(state))
            except OSError as exc:
                logger.warning("vl-worker-%d restart spawn failed: %s", state.index, exc)
                continue

    async def start(self) -> None:
        results = await asyncio.gather(
            *(self._start_worker(i, device) for i, device in enumerate(self._gpu_devices)),
            return_exceptions=True,
        )
        failures = [(i, r) for i, r in enumerate(results) if isinstance(r, Exception)]
        for i, exc in failures:
            logger.warning("vl-worker-%d failed to start: %s", i, exc)

        self.worker_count = len(self._gpu_devices) - len(failures)
        self.ready = self.worker_count > 0
        if not self.ready:
            logger.error("LocalLlamaServerPool: 0-of-%d workers healthy", len(self._gpu_devices))

    async def acquire(self) -> PoolWorker:
        return await self._queue.get()

    def release(self, worker: PoolWorker) -> None:
        state = self._states.get(worker.index)
        if state is not None and state.dead:
            return
        self._queue.put_nowait(worker)

    async def _terminate_proc(self, proc: asyncio.subprocess.Process) -> None:
        if proc.returncode is not None:
            return
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=_SHUTDOWN_WAIT_TIMEOUT_S)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()

    async def aclose(self) -> None:
        async with self._close_lock:
            if self._closed:
                return
            self._closed = True

            for state in self._states.values():
                if state.supervisor_task is not None:
                    state.supervisor_task.cancel()
                for task in state.drain_tasks:
                    task.cancel()

            for state in self._states.values():
                if state.supervisor_task is not None:
                    try:
                        await state.supervisor_task
                    except (asyncio.CancelledError, Exception):  # noqa: BLE001
                        pass
                for task in state.drain_tasks:
                    try:
                        await task
                    except (asyncio.CancelledError, Exception):  # noqa: BLE001
                        pass

            for state in self._states.values():
                if state.proc is not None:
                    state.stopping_intentionally = True
                    await self._terminate_proc(state.proc)

            self.ready = False
            self.worker_count = 0


class RemoteInferencePool:
    """Wraps pre-configured, already-reachable endpoints (a remote
    sglang/vLLM/other deployment) — no subprocess spawned, no health-check
    performed; a misconfigured URL fails normally through the LLMFactory
    client on first use, same as any other misconfigured remote endpoint in
    this codebase. Implements :class:`InferencePool`."""

    def __init__(self, endpoints: list[InferenceEndpoint], *, layout_devices: list[str]) -> None:
        self._endpoints = endpoints
        self._layout_devices = layout_devices
        self.ready = True
        self.worker_count = len(endpoints)
        self._queue: asyncio.Queue[PoolWorker] = asyncio.Queue()
        for i, endpoint in enumerate(endpoints):
            layout_device = layout_devices[i % len(layout_devices)] if layout_devices else "cpu"
            self._queue.put_nowait(PoolWorker(index=i, endpoint=endpoint, layout_device=layout_device))

    async def start(self) -> None:
        return None

    async def acquire(self) -> PoolWorker:
        return await self._queue.get()

    def release(self, worker: PoolWorker) -> None:
        self._queue.put_nowait(worker)

    async def aclose(self) -> None:
        return None


__all__ = ["LocalLlamaServerPool", "RemoteInferencePool"]
