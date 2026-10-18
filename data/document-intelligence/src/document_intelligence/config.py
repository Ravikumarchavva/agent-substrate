"""Environment-based configuration for the document-intelligence service.

All settings are read from environment variables with the
``DOCUMENT_INTELLIGENCE_`` prefix. Every per-mode field below defaults to
``None`` — "let ``autoconfig.resolve_runtime`` decide from detected
hardware" — an explicit env var always overrides that decision (the same
same-level-explicit-always-wins precedence PaddleX's own config resolver
uses; see ``autoconfig.py``'s ``resolve_child_setting`` docstring).
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

    # ── Extraction mode ──────────────────────────────────────────────────
    # "auto" (default) -- autoconfig.resolve_runtime() picks vl_gpu if an
    # eligible GPU is present, else vl_cpu if RAM allows, else raw_text.
    # This is a deployment-level choice (which engine THIS pod serves );
    # it is distinct from any future narrow per-request mode hint on
    # /v1/extract ("auto"|"raw_text"|"vl" -- never vl_cpu/vl_gpu
    # specifically, since that's a hardware fact, not a caller choice) --
    # not yet implemented, since today's architecture is one
    # hardware-resolved engine per pod, matching how PPStructureV3 was
    # already deployed before this redesign.
    mode: Literal["auto", "raw_text", "vl_cpu", "vl_gpu", "ocr_classic"] = "auto"

    max_upload_bytes: int = 50 * 1024 * 1024

    # ── mode "ocr_classic" (PPStructureV3) ─────────────────────────────
    # "tiny" is the fastest AND cleanest of the PP-OCRv6 sizes measured
    # against real scanned financial filings — NOT the smallest-but-worse
    # option. Real numbers: tiny 11.5s/page with clean text; small/medium
    # were both SLOWER (32-34s/page) and had word-spacing glitches tiny
    # didn't. Bigger was measurably worse here, not a size/quality tradeoff.
    ocr_size: Literal["tiny", "small", "medium"] = "tiny"
    # Explicit device override ("cpu" | "gpu:N"). None (the default) lets
    # autoconfig pick gpu:0 if hardware.detect() found an eligible GPU,
    # else cpu -- see engines/factory.py's "explicit wins" application.
    device: str | None = None
    # text_recognition_batch_size/textline_orientation_batch_size passed to
    # PPStructureV3 — how many OCR'd text regions get batched into one GPU
    # inference call instead of dozens of tiny sequential ones. 16 was
    # tuned for this project's original 4GB-class dev GPU (PP-StructureV3
    # holds layout+table+OCR models resident at once, so headroom is
    # tighter than a single-model server); a 24GB+ card has real room to
    # go much higher. None on CPU regardless of this value.
    ocr_batch_size: int = 16

    # Real, found-not-assumed: a genuine 267-page PDF hit "CUDA out of
    # memory... 18.50 MiB is free" partway through a single predict() call
    # on a 4GB card, even with ocr_batch_size correctly tuned — PaddleX
    # holds state across the whole document's pages within one predict()
    # call regardless of that setting. Above this many pages, extract()
    # splits the document into page-range chunks and calls predict() once
    # per chunk instead of once for the whole file, bounding peak memory
    # to one chunk regardless of total document length. None disables
    # chunking (single predict() call, the original behavior) — the
    # default whenever unset, on CPU or GPU.
    max_pages_per_call: int | None = None

    @field_validator("max_pages_per_call", mode="before")
    @classmethod
    def _empty_string_means_unset_pages(cls, v: object) -> object:
        # docker-compose's `${VAR:-}` always sets *some* value — an unset
        # override becomes an empty string, not a missing env var, which
        # pydantic otherwise rejects outright for `int | None` ("unable to
        # parse string as an integer"). Real crash, hit deploying this via
        # docker-compose.yml's own `${DOCUMENT_INTELLIGENCE_MAX_PAGES_PER_CALL:-}`
        # default before this validator existed.
        return None if v == "" else v

    # ── modes "vl_cpu" / "vl_gpu" (PaddleOCR-VL-1.6 over llama-server) ──
    # A remote, pre-existing OpenAI-compatible deployment (sglang/vLLM/
    # another llama-server) — when set, no local llama-server subprocess
    # is ever spawned. This IS the proof Phase 0's InferenceEndpoint seam
    # is real for this engine, not just chat models: "today llama.cpp,
    # tomorrow a remote URL" becomes one env var, zero code change.
    vl_remote_base_url: str | None = None
    vl_remote_timeout_s: float = 90.0
    vl_model_dir: str = "/models/paddleocr-vl"
    vl_hf_repo: str = "PaddlePaddle/PaddleOCR-VL-1.6-GGUF"
    llama_server_bin: str = "llama-server"
    llama_quantize_bin: str = "llama-quantize"
    vl_base_port: int = 8090
    # Q8_0 is the only safe default for both the main LM and the vision
    # encoder — Q4_K_M was directly proven this session (a real A/B test,
    # identical real page) to corrupt PaddleOCR-VL's structured output (a
    # malformed-output parser error at Q4, clean at Q8_0). Q4 stays an
    # explicit opt-in; see models.py::_warn_if_q4 for the loud startup
    # warning it triggers. None here means "use autoconfig's verified
    # q8_0 default", not "unset".
    vl_quant: str | None = None
    vl_ctx_size: int | None = None
    vl_slots: int | None = None
    vl_threads: int | None = None
    vl_max_new_tokens: int | None = None
    vl_startup_timeout_s: float = 300.0
    vl_max_restarts: int = 5
    # LayoutDetection/DocPreprocessor batch_size override for the VL
    # pipeline (layout detection is still classic Paddle models inside
    # PaddleOCR-VL — see engines/paddle_vl.py's module docstring). None
    # means "use autoconfig's verified batch_size=4 (or the >=16GB-card
    # scaled value)".
    layout_batch_size: int | None = None

    # ── Document security scan (doc-firewall, security_scan.py) ──────────
    # Runs on raw bytes before any engine parses them — see routes.py.
    # True by default; disable only for local debugging of the extraction
    # pipeline itself.
    enable_document_security_scan: bool = True

    # ── Pod identity (k8s Downward API) ──────────────────────────────────
    pod_name: str = "document-intelligence-0"

    model_config = {"env_prefix": "DOCUMENT_INTELLIGENCE_"}
