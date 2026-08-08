"""Environment-based configuration for the document-extraction service.

All settings are read from environment variables with the ``EXTRACTION_``
prefix.
"""

from __future__ import annotations

from typing import Literal

from pydantic_settings import BaseSettings


class ServiceConfig(BaseSettings):
    """Document-extraction service configuration."""

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

    # ── Multimodal embedding + reranker ─────────────────────────────────
    embedding_model: str = "google/siglip-base-patch16-224"
    embedding_dim: int = 768
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"

    # ── Pod identity (k8s Downward API) ──────────────────────────────────
    pod_name: str = "extraction-0"

    model_config = {"env_prefix": "EXTRACTION_"}
