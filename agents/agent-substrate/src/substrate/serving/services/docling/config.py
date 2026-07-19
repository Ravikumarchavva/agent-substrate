"""Environment-based configuration for the Docling extraction service.

All settings are read from environment variables with the ``DOCLING_``
prefix.
"""

from __future__ import annotations

from typing import Literal

from pydantic_settings import BaseSettings


class ServiceConfig(BaseSettings):
    """Docling extraction service configuration."""

    # ── Server ───────────────────────────────────────────────────────────
    host: str = "0.0.0.0"
    port: int = 8080

    # ── Inter-service auth ───────────────────────────────────────────────
    auth_token: str = ""

    # ── Extraction ───────────────────────────────────────────────────────
    ocr_enabled: bool = False  # slow — opt in explicitly, not the default
    output_format: Literal["blocks", "markdown", "html"] = "markdown"
    max_upload_bytes: int = 50 * 1024 * 1024

    # ── Pod identity (k8s Downward API) ──────────────────────────────────
    pod_name: str = "docling-0"

    model_config = {"env_prefix": "DOCLING_"}
