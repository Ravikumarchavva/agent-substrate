"""Real subprocess tests for ``LocalLlamaServerPool``/``RemoteInferencePool``
against a stub ``llama-server`` (``_stub_llama_server.py``) — no GPU, no real
model, no real llama-server binary needed. This is deliberately the
highest-leverage testability decision in the Phase 1 design: everything
about spawn/health/dispatch/supervise/shutdown is exercised for real, just
against a fake process instead of a heavy one.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from substrate.integrations.llm.endpoint import InferenceEndpoint
from substrate.runtimes.inference_pool.llama_pool import (
    LocalLlamaServerPool,
    RemoteInferencePool,
)
from substrate.runtimes.inference_pool.pool_types import PoolWorker

_STUB = str(Path(__file__).parent / "_stub_llama_server.py")


def _make_pool(gpu_devices: list[str], **kwargs) -> LocalLlamaServerPool:
    return LocalLlamaServerPool(
        binary=sys.executable,
        main_gguf="/dev/null",  # unused — stub ignores every arg but --port
        mmproj_gguf="/dev/null",
        gpu_devices=gpu_devices,
        base_port=kwargs.pop("base_port", 18090),
        startup_timeout_s=kwargs.pop("startup_timeout_s", 10.0),
        max_restarts=kwargs.pop("max_restarts", 2),
        **kwargs,
    )


def _argv_patch(monkeypatch: pytest.MonkeyPatch, pool: LocalLlamaServerPool) -> None:
    """The stub is a plain Python script, not a binary named "llama-server"
    — rewrite _argv() to invoke it via `sys.executable <script> --port N`
    instead of pool._binary directly."""
    original = pool._argv

    def patched(port: int) -> list[str]:
        real_argv = original(port)
        # real_argv[0] is sys.executable (we passed it as `binary`); insert
        # the stub script path right after so it becomes
        # `<python> _stub_llama_server.py --port N ...`
        return [real_argv[0], _STUB, *real_argv[1:]]

    monkeypatch.setattr(pool, "_argv", patched)


async def test_pool_starts_n_workers_and_becomes_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = _make_pool(["gpu:0", "gpu:1"], base_port=18100)
    _argv_patch(monkeypatch, pool)
    try:
        await pool.start()
        assert pool.ready is True
        assert pool.worker_count == 2
    finally:
        await pool.aclose()


async def test_acquire_release_cycle_returns_a_real_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = _make_pool(["gpu:0"], base_port=18110)
    _argv_patch(monkeypatch, pool)
    try:
        await pool.start()
        worker = await asyncio.wait_for(pool.acquire(), timeout=5.0)
        assert isinstance(worker, PoolWorker)
        assert isinstance(worker.endpoint, InferenceEndpoint)
        assert worker.endpoint.base_url == "http://127.0.0.1:18110"
        assert worker.layout_device == "gpu:0"
        pool.release(worker)
        # Must be immediately re-acquirable after release.
        worker2 = await asyncio.wait_for(pool.acquire(), timeout=5.0)
        assert worker2.index == worker.index
    finally:
        await pool.aclose()


async def test_concurrent_acquires_never_double_hand_out_the_same_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = _make_pool(["gpu:0", "gpu:1"], base_port=18120)
    _argv_patch(monkeypatch, pool)
    try:
        await pool.start()
        held: set[int] = set()
        overlap_detected = False

        async def _do_work() -> None:
            nonlocal overlap_detected
            worker = await pool.acquire()
            if worker.index in held:
                overlap_detected = True
            held.add(worker.index)
            await asyncio.sleep(0.05)
            held.discard(worker.index)
            pool.release(worker)

        # 6 concurrent "requests" against a 2-worker pool.
        await asyncio.gather(*(_do_work() for _ in range(6)))
        assert overlap_detected is False
    finally:
        await pool.aclose()


async def test_worker_crash_triggers_restart_or_degrades_worker_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = _make_pool(["gpu:0"], base_port=18130, max_restarts=2)
    _argv_patch(monkeypatch, pool)
    try:
        await pool.start()
        assert pool.worker_count == 1
        state = pool._states[0]
        proc = state.proc
        assert proc is not None

        # Kill the child out from under the pool.
        proc.kill()
        await proc.wait()

        # Give the supervisor task time to notice, back off, and restart.
        for _ in range(50):
            await asyncio.sleep(0.2)
            if state.proc is not proc and state.proc is not None:
                break

        # Either it restarted (new proc object, still healthy/queued) or it
        # exhausted max_restarts and marked itself dead — both are correct
        # outcomes of the supervision loop; what must NOT happen is the
        # pool hanging forever or crashing.
        assert state.proc is not proc or state.dead is True
    finally:
        await pool.aclose()


async def test_aclose_leaves_no_orphaned_children(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = _make_pool(["gpu:0", "gpu:1"], base_port=18140)
    _argv_patch(monkeypatch, pool)
    await pool.start()
    procs = [state.proc for state in pool._states.values() if state.proc is not None]
    assert len(procs) == 2

    await pool.aclose()

    for proc in procs:
        assert proc.returncode is not None, "child process was not reaped on aclose()"


async def test_aclose_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = _make_pool(["gpu:0"], base_port=18150)
    _argv_patch(monkeypatch, pool)
    await pool.start()
    await pool.aclose()
    await pool.aclose()  # must not raise or hang the second time


async def test_zero_of_n_workers_healthy_leaves_pool_not_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point at a binary that will genuinely fail to bind/health-check in
    time — a real, guaranteed-to-be-missing binary name triggers
    FileNotFoundError inside _spawn, which _start_worker lets propagate,
    and start()'s gather(return_exceptions=True) must turn that into a
    0-of-N-ready pool rather than raising out of start() itself."""
    pool = LocalLlamaServerPool(
        binary="/definitely/not/a/real/binary/llama-server",
        main_gguf="/dev/null",
        mmproj_gguf="/dev/null",
        gpu_devices=["gpu:0"],
        base_port=18160,
        startup_timeout_s=2.0,
    )
    await pool.start()
    assert pool.ready is False
    assert pool.worker_count == 0
    await pool.aclose()


# ── generic argv (main_gguf/mmproj_gguf optional, extra_args) ──────────────


def test_argv_includes_m_and_mmproj_when_both_given() -> None:
    pool = LocalLlamaServerPool(
        binary=sys.executable,
        main_gguf="/models/main.gguf",
        mmproj_gguf="/models/mmproj.gguf",
        gpu_devices=["gpu:0"],
        base_port=18090,
    )
    argv = pool._argv(18090)
    assert "-m" in argv and argv[argv.index("-m") + 1] == "/models/main.gguf"
    assert "--mmproj" in argv and argv[argv.index("--mmproj") + 1] == "/models/mmproj.gguf"


def test_argv_omits_m_and_mmproj_when_both_none() -> None:
    """The real fix this covers: a consumer that serves via --hf-repo/
    --hf-file (embedding_reranker's local mode) must not get a spurious
    `-m None --mmproj None` in its argv."""
    pool = LocalLlamaServerPool(
        binary=sys.executable,
        gpu_devices=["gpu:0"],
        base_port=18090,
    )
    argv = pool._argv(18090)
    assert "-m" not in argv
    assert "--mmproj" not in argv


def test_argv_appends_extra_args_after_universal_flags() -> None:
    pool = LocalLlamaServerPool(
        binary=sys.executable,
        gpu_devices=["gpu:0"],
        base_port=18090,
        slots=2,
        extra_args=["--hf-repo", "org/Repo-GGUF", "--hf-file", "model.gguf", "--embedding"],
    )
    argv = pool._argv(18090)
    assert argv[-5:] == ["--hf-repo", "org/Repo-GGUF", "--hf-file", "model.gguf", "--embedding"]
    # slots still covers -np, no double --parallel from extra_args in this test
    assert "-np" in argv and argv[argv.index("-np") + 1] == "2"


async def test_pool_with_no_gguf_files_and_extra_args_still_spawns_and_becomes_healthy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real, end-to-end (subprocess-level, via the same stub every other
    test here uses) proof that a PaddleOCR-VL-shaped gguf pair is NOT
    required — a pool built the way embedding_reranker's local mode
    builds one (main_gguf/mmproj_gguf both None, real flags via
    extra_args) genuinely spawns and health-gates correctly."""
    pool = LocalLlamaServerPool(
        binary=sys.executable,
        gpu_devices=["gpu:0"],
        base_port=18170,
        startup_timeout_s=10.0,
        extra_args=["--hf-repo", "org/Repo-GGUF", "--hf-file", "model.gguf", "--embedding"],
    )
    _argv_patch(monkeypatch, pool)
    await pool.start()
    try:
        assert pool.ready is True
        assert pool.worker_count == 1
    finally:
        await pool.aclose()


async def test_remote_pool_cycles_preconfigured_endpoints() -> None:
    endpoints = [
        InferenceEndpoint(model="compatible/PaddleOCR-VL-1.6", base_url="http://vl-a:9000"),
        InferenceEndpoint(model="compatible/PaddleOCR-VL-1.6", base_url="http://vl-b:9000"),
    ]
    pool = RemoteInferencePool(endpoints, layout_devices=["cpu"])
    await pool.start()

    assert pool.ready is True
    assert pool.worker_count == 2

    w1 = await pool.acquire()
    w2 = await pool.acquire()
    assert {w1.endpoint.base_url, w2.endpoint.base_url} == {"http://vl-a:9000", "http://vl-b:9000"}
    pool.release(w1)
    pool.release(w2)

    await pool.aclose()  # no-op, must not raise
