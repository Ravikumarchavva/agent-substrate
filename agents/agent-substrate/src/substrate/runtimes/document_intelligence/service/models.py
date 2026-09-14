"""GGUF model provisioning for the ``vl_cpu``/``vl_gpu`` llama.cpp pool.

``ensure_models()`` resolves the two GGUF files the VL engine needs (the
main LM and the mmproj vision encoder) via a three-tier priority order:

1. **Baked-image fast path** — both quantized files already exist on disk
   (the expected case in a real deployed image, quantized at Docker build
   time).
2. **Pre-quantized HuggingFace upload** — the upstream repo already ships a
   quantized GGUF, so both the FP16 download and the local quantize step
   are skipped.
3. **Download FP16 + quantize locally** — the real fallback path, verified
   working this session: download the FP16 originals, then shell out to
   the real ``llama-quantize`` CLI (not a Python library) once per file.

Q8_0 is the only safe default. Q4_K_M was directly proven this session (a
real A/B test, identical real page) to corrupt PaddleOCR-VL's structured
output — a malformed-output parser error at Q4 that was clean at Q8_0 —
so Q4 stays an explicit opt-in with a loud startup warning, never silent.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from substrate.logger import setup_logging

logger = setup_logging("substrate.document_intelligence.models")

_MAIN_FP16_FILENAME = "PaddleOCR-VL-1.6-GGUF.gguf"
_MMPROJ_FP16_FILENAME = "PaddleOCR-VL-1.6-GGUF-mmproj.gguf"

_QUANTIZE_TIMEOUT_S = 600


def _quantized_filename(quant: str, *, mmproj: bool) -> str:
    suffix = "-mmproj" if mmproj else ""
    return f"PaddleOCR-VL-1.6-{quant}{suffix}.gguf"


def _existing_and_nonempty(path: Path) -> bool:
    return path.exists() and path.stat().st_size > 0


def _warn_if_q4(quant: str) -> None:
    if quant != "q4_k_m":
        return
    logger.warning(
        "DOCUMENT_INTELLIGENCE_VL_QUANT=q4_k_m requested. This session's own "
        "A/B test on real content directly proved Q4_K_M quantization "
        "corrupts PaddleOCR-VL's structured output: a real page produced a "
        "malformed-output parser error at Q4 that was clean at Q8_0. Q8_0 is "
        "the only safe default -- use Q4 only if you have independently "
        "re-verified it against your own content."
    )
    logger.warning(
        "The mmproj (vision encoder) GGUF has never been tested at Q4_K_M at "
        "all -- this session only validated Q8_0 for mmproj and Q4_K_M for "
        "the main LM separately, never that combination together."
    )


async def _run_quantize(
    llama_quantize_bin: str,
    input_path: Path,
    output_path: Path,
    quant: str,
) -> None:
    """Shell out to the real ``llama-quantize`` CLI for one GGUF file.

    Raises ``RuntimeError`` with the captured stdout/stderr if the process
    exits non-zero, times out, or doesn't actually produce the output file.
    """
    proc = await asyncio.create_subprocess_exec(
        llama_quantize_bin,
        str(input_path),
        str(output_path),
        quant.upper(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=_QUANTIZE_TIMEOUT_S
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError(
            f"llama-quantize timed out after {_QUANTIZE_TIMEOUT_S}s "
            f"quantizing {input_path} -> {output_path}"
        )

    if proc.returncode != 0 or not _existing_and_nonempty(output_path):
        raise RuntimeError(
            f"llama-quantize failed (exit {proc.returncode}) quantizing "
            f"{input_path} -> {output_path} at {quant.upper()}\n"
            f"stdout: {stdout.decode(errors='replace')}\n"
            f"stderr: {stderr.decode(errors='replace')}"
        )


def _match_quantized_filename(filenames: list[str], quant: str) -> str | None:
    """Find a filename on HF that looks like a pre-quantized upload for
    ``quant`` (naming conventions vary -- check reasonable case-insensitive
    variants like ``Q8_0``/``q8_0``)."""
    needle_upper = quant.upper()
    needle_lower = quant.lower()
    for name in filenames:
        if not name.endswith(".gguf"):
            continue
        if needle_upper in name or needle_lower in name:
            return name
    return None


async def ensure_models(
    model_dir: str,
    quant: str = "q8_0",
    *,
    llama_quantize_bin: str = "llama-quantize",
    hf_repo: str = "PaddlePaddle/PaddleOCR-VL-1.6-GGUF",
) -> tuple[str, str]:
    """Resolve local paths to the main LM and mmproj GGUF files, ready to
    hand to ``llama-server``.

    Never leaves the caller with a half-downloaded/half-quantized file --
    either both paths are complete, valid files, or this raises (model
    provisioning failure is a real startup problem the caller must know
    about, not something to silently degrade past).
    """
    _warn_if_q4(quant)

    model_dir_path = Path(model_dir)
    model_dir_path.mkdir(parents=True, exist_ok=True)

    main_path = model_dir_path / _quantized_filename(quant, mmproj=False)
    mmproj_path = model_dir_path / _quantized_filename(quant, mmproj=True)

    # Tier 1 -- baked-image fast path.
    if _existing_and_nonempty(main_path) and _existing_and_nonempty(mmproj_path):
        logger.info(
            "GGUF models already present at %s (%s) -- skipping download/quantize.",
            main_path,
            mmproj_path,
        )
        return str(main_path), str(mmproj_path)

    from huggingface_hub import HfApi, hf_hub_download

    # Tier 2 -- pre-quantized upload already on HuggingFace.
    repo_files = HfApi().list_repo_files(hf_repo)
    main_hf_name = _match_quantized_filename(repo_files, quant)
    mmproj_candidates = [f for f in repo_files if "mmproj" in f.lower()]
    mmproj_hf_name = _match_quantized_filename(mmproj_candidates, quant)

    if main_hf_name and mmproj_hf_name:
        logger.info(
            "Found pre-quantized %s uploads on %s: %s, %s",
            quant.upper(),
            hf_repo,
            main_hf_name,
            mmproj_hf_name,
        )
        downloaded_main = hf_hub_download(
            repo_id=hf_repo, filename=main_hf_name, local_dir=str(model_dir_path)
        )
        downloaded_mmproj = hf_hub_download(
            repo_id=hf_repo, filename=mmproj_hf_name, local_dir=str(model_dir_path)
        )
        return downloaded_main, downloaded_mmproj

    # Tier 3 -- download FP16 originals, then quantize locally.
    logger.info(
        "No pre-quantized %s upload found on %s -- downloading FP16 originals "
        "and quantizing locally via %s.",
        quant.upper(),
        hf_repo,
        llama_quantize_bin,
    )
    main_fp16_path = Path(
        hf_hub_download(
            repo_id=hf_repo,
            filename=_MAIN_FP16_FILENAME,
            local_dir=str(model_dir_path),
        )
    )
    mmproj_fp16_path = Path(
        hf_hub_download(
            repo_id=hf_repo,
            filename=_MMPROJ_FP16_FILENAME,
            local_dir=str(model_dir_path),
        )
    )

    await _run_quantize(llama_quantize_bin, main_fp16_path, main_path, quant)
    await _run_quantize(llama_quantize_bin, mmproj_fp16_path, mmproj_path, quant)

    return str(main_path), str(mmproj_path)
