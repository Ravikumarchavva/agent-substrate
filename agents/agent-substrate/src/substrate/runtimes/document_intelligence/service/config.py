"""Environment-based configuration for the document-intelligence service.

All settings are read from environment variables with the
``DOCUMENT_INTELLIGENCE_`` prefix.
"""

from __future__ import annotations

from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings


class ServiceConfig(BaseSettings):
    """Document-intelligence service configuration."""

    # ── Server ───────────────────────────────────────────────────────────
    host: str = "0.0.0.0"
    port: int = 8080

    # ── Inter-service auth ───────────────────────────────────────────────
    auth_token: str = ""

    # ── Extraction (layout/chart detection + OCR, via PaddleOCR) ─────────
    # "tiny" is the fastest AND cleanest of the PP-OCRv6 sizes measured
    # against real scanned financial filings — NOT the smallest-but-worse
    # option. Real numbers: tiny 11.5s/page with clean text; small/medium
    # were both SLOWER (32-34s/page) and had word-spacing glitches tiny
    # didn't. Bigger was measurably worse here, not a size/quality tradeoff.
    ocr_size: Literal["tiny", "small", "medium"] = "tiny"
    max_upload_bytes: int = 50 * 1024 * 1024

    # "cpu" (default — cheap to host, no CUDA image) or e.g. "gpu:0" for
    # local dev on an NVIDIA GPU. GPU requires the `document-intelligence-gpu`
    # install extra (paddlepaddle-gpu, matching CUDA index — see
    # pyproject.toml) instead of the default CPU wheel; passed straight
    # through to PPStructureV3(device=...) in pipeline.py.
    device: str = "cpu"

    # text_recognition_batch_size/textline_orientation_batch_size passed to
    # PPStructureV3 (pipeline.py) — how many OCR'd text regions get batched
    # into one GPU inference call instead of dozens of tiny sequential
    # ones. 16 was tuned for this project's original 4GB-class dev GPU
    # (PP-StructureV3 holds layout+table+OCR models resident at once, so
    # headroom is tighter than a single-model server); a 24GB+ card has
    # real room to go much higher. None on CPU regardless of this value —
    # pipeline.py only applies it when device starts with "gpu".
    ocr_batch_size: int = 16

    # Real, found-not-assumed: a genuine 267-page PDF hit "CUDA out of
    # memory... 18.50 MiB is free" partway through a single predict() call
    # on a 4GB card, even with ocr_batch_size correctly tuned — PaddleX
    # holds state across the whole document's pages within one predict()
    # call regardless of that setting (community-reported ballpark:
    # ~1GB base + ~200MB/page on GPU with the full model stack loaded,
    # consistent with what actually happened here). Above this many pages,
    # extract() (pipeline.py) splits the document into page-range chunks
    # and calls predict() once per chunk instead of once for the whole
    # file, bounding peak memory to one chunk regardless of total document
    # length. None on CPU (ample system RAM, not the 4GB VRAM ceiling this
    # was tuned against) and whenever unset for GPU too — only a default
    # here, not a hard requirement.
    max_pages_per_call: int | None = None

    @field_validator("max_pages_per_call", mode="before")
    @classmethod
    def _empty_string_means_unset(cls, v: object) -> object:
        # docker-compose's `${VAR:-}` always sets *some* value — an unset
        # override becomes an empty string, not a missing env var, which
        # pydantic otherwise rejects outright for `int | None` ("unable to
        # parse string as an integer"). Real crash, hit deploying this via
        # docker-compose.yml's own `${DOCUMENT_INTELLIGENCE_MAX_PAGES_PER_CALL:-}`
        # default before this validator existed.
        return None if v == "" else v

    # ── Document security scan (doc-firewall, security_scan.py) ──────────
    # Runs on raw bytes before PaddleOCR/PaddleX parses them — see
    # routes.py::extract(). True by default; disable only for local
    # debugging of the extraction pipeline itself.
    enable_document_security_scan: bool = True

    # ── Pod identity (k8s Downward API) ──────────────────────────────────
    pod_name: str = "document-intelligence-0"

    model_config = {"env_prefix": "DOCUMENT_INTELLIGENCE_"}
