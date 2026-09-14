"""Unit tests for models.py's GGUF provisioning tiers.

All tests use ``tmp_path`` as ``model_dir`` and mock/monkeypatch network
(``huggingface_hub``) and subprocess (``llama-quantize``) calls -- never
touch real disk locations outside the test's own temp dir, never hit the
network, never run a real binary.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import huggingface_hub
import pytest

from substrate.runtimes.document_intelligence.service import models


def _fail_hf_hub_download(*args: object, **kwargs: object) -> str:
    raise AssertionError("hf_hub_download should not have been called")


def _fail_create_subprocess_exec(*args: object, **kwargs: object):
    raise AssertionError("asyncio.create_subprocess_exec should not have been called")


class _FakeProcess:
    def __init__(self, returncode: int, stdout: bytes = b"", stderr: bytes = b"") -> None:
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, self._stderr

    def kill(self) -> None:
        pass

    async def wait(self) -> int:
        return self.returncode


async def test_tier1_uses_existing_files_without_download_or_quantize(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    main_path = tmp_path / "PaddleOCR-VL-1.6-q8_0.gguf"
    mmproj_path = tmp_path / "PaddleOCR-VL-1.6-q8_0-mmproj.gguf"
    main_path.write_bytes(b"fake main gguf")
    mmproj_path.write_bytes(b"fake mmproj gguf")

    monkeypatch.setattr(huggingface_hub, "hf_hub_download", _fail_hf_hub_download)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fail_create_subprocess_exec)

    result_main, result_mmproj = await models.ensure_models(str(tmp_path), quant="q8_0")

    assert result_main == str(main_path)
    assert result_mmproj == str(mmproj_path)


async def test_tier2_downloads_preexisting_quantized_upload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    main_hf_name = "PaddleOCR-VL-1.6-GGUF.Q8_0.gguf"
    mmproj_hf_name = "PaddleOCR-VL-1.6-GGUF-mmproj.Q8_0.gguf"

    monkeypatch.setattr(
        huggingface_hub.HfApi,
        "list_repo_files",
        lambda self, repo_id: [main_hf_name, mmproj_hf_name, "PaddleOCR-VL-1.6-GGUF.gguf"],
    )

    def _fake_download(*, repo_id: str, filename: str, local_dir: str) -> str:
        path = Path(local_dir) / filename
        path.write_bytes(b"fake quantized upload")
        return str(path)

    monkeypatch.setattr(huggingface_hub, "hf_hub_download", _fake_download)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fail_create_subprocess_exec)

    result_main, result_mmproj = await models.ensure_models(str(tmp_path), quant="q8_0")

    assert result_main == str(tmp_path / main_hf_name)
    assert result_mmproj == str(tmp_path / mmproj_hf_name)


async def test_tier3_downloads_fp16_and_quantizes_locally(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Only FP16 originals available on HF -- no quant match.
    monkeypatch.setattr(
        huggingface_hub.HfApi,
        "list_repo_files",
        lambda self, repo_id: [
            "PaddleOCR-VL-1.6-GGUF.gguf",
            "PaddleOCR-VL-1.6-GGUF-mmproj.gguf",
        ],
    )

    def _fake_download(*, repo_id: str, filename: str, local_dir: str) -> str:
        path = Path(local_dir) / filename
        path.write_bytes(b"fake fp16 content")
        return str(path)

    monkeypatch.setattr(huggingface_hub, "hf_hub_download", _fake_download)

    quantize_calls: list[tuple[str, ...]] = []

    async def _fake_create_subprocess_exec(*args: str, **kwargs: object) -> _FakeProcess:
        quantize_calls.append(args)
        # args: (bin, input_path, output_path, quant_type)
        output_path = Path(args[2])
        output_path.write_bytes(b"fake quantized output")
        return _FakeProcess(returncode=0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)

    result_main, result_mmproj = await models.ensure_models(str(tmp_path), quant="q8_0")

    assert result_main == str(tmp_path / "PaddleOCR-VL-1.6-q8_0.gguf")
    assert result_mmproj == str(tmp_path / "PaddleOCR-VL-1.6-q8_0-mmproj.gguf")
    assert Path(result_main).exists()
    assert Path(result_mmproj).exists()
    assert len(quantize_calls) == 2


async def test_quantize_failure_raises_runtime_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        huggingface_hub.HfApi,
        "list_repo_files",
        lambda self, repo_id: [
            "PaddleOCR-VL-1.6-GGUF.gguf",
            "PaddleOCR-VL-1.6-GGUF-mmproj.gguf",
        ],
    )

    def _fake_download(*, repo_id: str, filename: str, local_dir: str) -> str:
        path = Path(local_dir) / filename
        path.write_bytes(b"fake fp16 content")
        return str(path)

    monkeypatch.setattr(huggingface_hub, "hf_hub_download", _fake_download)

    async def _fake_create_subprocess_exec(*args: str, **kwargs: object) -> _FakeProcess:
        return _FakeProcess(returncode=1, stderr=b"quantize exploded")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)

    with pytest.raises(RuntimeError):
        await models.ensure_models(str(tmp_path), quant="q8_0")


async def test_q4_requests_logs_loud_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Reuse the Tier 1 fast path so nothing else needs mocking.
    main_path = tmp_path / "PaddleOCR-VL-1.6-q4_k_m.gguf"
    mmproj_path = tmp_path / "PaddleOCR-VL-1.6-q4_k_m-mmproj.gguf"
    main_path.write_bytes(b"fake main gguf")
    mmproj_path.write_bytes(b"fake mmproj gguf")

    monkeypatch.setattr(huggingface_hub, "hf_hub_download", _fail_hf_hub_download)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fail_create_subprocess_exec)

    # caplog can't see this: setup_logging() sets propagate=False on the
    # "substrate" namespace (real, found this session — its own file
    # handler works fine, but that means pytest's caplog, which captures
    # via root-logger propagation, never receives these records regardless
    # of caplog.at_level(logger=...)). Monkeypatching the module's logger
    # directly is the reliable way to assert on log content in this
    # codebase until that's fixed elsewhere.
    warnings: list[str] = []
    monkeypatch.setattr(
        models.logger, "warning", lambda msg, *a, **kw: warnings.append(msg % a if a else msg)
    )

    await models.ensure_models(str(tmp_path), quant="q4_k_m")

    assert any("corrupt" in w.lower() or "malformed" in w.lower() for w in warnings)
