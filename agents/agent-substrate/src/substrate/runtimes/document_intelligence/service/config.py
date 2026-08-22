"""Environment-based configuration for the document-intelligence service.

All settings are read from environment variables with the
``DOCUMENT_INTELLIGENCE_`` prefix.
"""

from __future__ import annotations

from typing import Literal

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

    # ── Document security scan (doc-firewall, security_scan.py) ──────────
    # Runs on raw bytes before PaddleOCR/PaddleX parses them — see
    # routes.py::extract(). True by default; disable only for local
    # debugging of the extraction pipeline itself.
    enable_document_security_scan: bool = True

    # ── Pod identity (k8s Downward API) ──────────────────────────────────
    pod_name: str = "document-intelligence-0"

    model_config = {"env_prefix": "DOCUMENT_INTELLIGENCE_"}
