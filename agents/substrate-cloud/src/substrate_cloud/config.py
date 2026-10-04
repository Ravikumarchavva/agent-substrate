"""Substrate library configuration.

``SubstrateConfig`` holds everything the library layer needs — API keys, model
defaults, storage URLs, and tool settings. It reads from environment variables
only (no ``.env`` file loading). Consumers instantiate it explicitly:

    cfg = SubstrateConfig(openai_api_key="sk-...")
    runtime = Runtime(config=cfg)

For running the built-in FastAPI server, use
``substrate_cloud.shared.settings.ServerSettings`` instead — it extends this
class and adds server-only fields with ``.env`` file auto-loading.

For the complete reference of all settings and architecture, see
``docs/configuration.md``.
"""

from __future__ import annotations

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class SubstrateConfig(BaseSettings):
    # ── Single Data Directory (Unified Root) ──────────────────────────────────
    DATA_DIR: str = "./data"
    # "local" (zero-external-service file/LanceDB store) | "postgres" (PostgreSQL + S3) | "hybrid"
    STORAGE_MODE: str = "local"
    # ── LLM provider keys ────────────────────────────────────────────────────
    OPENAI_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""
    GEMINI_API_KEY: str = ""
    GROQ_API_KEY: str = ""
    NVIDIA_API_KEY: str = ""
    OPENROUTER_API_KEY: str = ""
    EXA_API_KEY: str = ""
    TAVILY_API_KEY: str = ""

    # ── Provider base URLs ───────────────────────────────────────────────────
    OPENAI_BASE_URL: str = ""
    GROQ_BASE_URL: str = "https://api.groq.com/openai/v1"
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
    OPENROUTER_SITE_URL: str = "http://localhost:3000"
    OPENROUTER_APP_NAME: str = "Agent Substrate"

    # HuggingFace token for downloading gated models/tokenizers in subprocesses
    HF_TOKEN: str = ""

    # ── Database & RLS ───────────────────────────────────────────────────────
    # Primary / admin connection (schema setup, migrations, superuser)
    DATABASE_URL: str = ""
    ASYNC_DATABASE_URL: str = ""
    # Restricted application connection used when RLS is provisioned
    APP_DATABASE_URL: str = ""
    RLS_APP_ROLE_PASSWORD: str | None = None

    # ── The store's PostgreSQL pool and schema (STORE_BACKEND=postgres) ──────
    STORE_PG_POOL_MIN_SIZE: int = 2
    STORE_PG_POOL_MAX_SIZE: int = 10
    STORE_PG_SCHEMA: str = (
        "substrate"  # the engine's tables live here, apart from the application's
    )

    # ── Redis ────────────────────────────────────────────────────────────────
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_SESSION_TTL: int = 3600

    # ── The store: everything the engine remembers ───────────────────────────
    # "postgres" (workers on several machines; rows in DATABASE_URL) | "local" (no infra: one folder, STORE_PATH).
    # File contents live under STORE_PATH either way unless FILE_STORE_BACKEND=s3.
    STORE_BACKEND: str = "postgres"
    STORE_PATH: str = ""

    # ── Session / context ────────────────────────────────────────────────────
    SESSION_MAX_MESSAGES: int = 200
    SESSION_AUTO_CHECKPOINT: int = 50

    # ── Model defaults ───────────────────────────────────────────────────────
    AGENT_MODE: str = "react"  # "react" | "orchestrator"
    CHAT_MODEL: str = "google/gemini-3.1-flash-lite"
    # A knowledge base is searched by meaning when it has an embedder: EMBEDDING_RERANKER_SERVICE_URL (the default stack: Qwen3-VL embedding
    # and reranking), or EMBEDDING_MODEL (a provider model, e.g. "openai/text-embedding-3-small"). With neither it is searched by words.
    EMBEDDING_MODEL: str = ""
    STT_MODEL: str = "whisper-1"
    TTS_MODEL: str = "local/kokoro-82m"
    TTS_VOICE: str = "Kore"  # a Gemini voice; Kokoro always uses af_heart
    REALTIME_MODEL: str = "gpt-4o-realtime-preview-2024-12-17"
    REALTIME_VOICE: str = "coral"
    MODEL_CONTEXT_WINDOW: int = 40

    # ── Semantic cache ───────────────────────────────────────────────────────
    SEMANTIC_CACHE_ENABLED: bool = False
    SEMANTIC_CACHE_THRESHOLD: float = 0.95
    SEMANTIC_CACHE_TTL: int = 3600

    # ── Web search ───────────────────────────────────────────────────────────
    WEB_SEARCH_MAX_RESULTS: int = 3
    WEB_SEARCH_MAX_CHARS: int = 5000
    WEB_READ_MAX_CHARS: int = 6000

    # ── Chat attachments ─────────────────────────────────────────────────────
    ATTACHMENT_PDF_MAX_CHARS: int = 20000

    # ── Tool behaviour ───────────────────────────────────────────────────────
    DISABLE_TOOL_APPROVALS: bool = False

    # ── File storage ─────────────────────────────────────────────────────────
    # "local" (Files) | "s3" (SeaweedFS/S3)
    FILE_STORE_BACKEND: str = "local"
    FILE_STORE_ROOT: str = ""
    FILE_STORE_BUCKET: str = "agent-files"
    FILE_STORE_ENDPOINT: str | None = None
    FILE_STORE_REGION: str = "us-east-1"
    FILE_STORE_ACCESS_KEY: str | None = None
    FILE_STORE_SECRET_KEY: str | None = None
    FILE_STORE_PREFIX: str = ""

    # ── Code interpreter sandbox ─────────────────────────────────────────────
    # "nsjail" (Linux namespaces/cgroups) | "k8s" (isolated pods) | "inprocess" (tests only)
    SANDBOX_RUNTIME: str = "nsjail"
    SANDBOX_NETWORK_POLICY: str = "deny"  # "deny" | "pip_only" | "full"
    SANDBOX_TIMEOUT_SECONDS: int = 60
    # Disposable local scratch StagedSandboxRuntime materializes a branch's
    # workspace snapshot into before a run and commits back after — never
    # the durable source of truth (that's the CAS + WorkspaceStore), safe to
    # wipe on restart. Deliberately separate from FILE_STORE_ROOT: the
    # object store holds content-addressed blobs now, not a browsable tree.
    SANDBOX_SCRATCH_ROOT: str = ""
    SANDBOX_MEMORY_BYTES: int = 2 * 1024 * 1024 * 1024
    SANDBOX_SESSION_TTL_SECONDS: int = 3600
    SANDBOX_RUNTIME_CLASS: str = ""
    SANDBOX_PYTHON: str = ""
    CI_WORKSPACE_PVC_CLAIM: str = ""

    # ── Document-intelligence service ────────────────────────────────────────
    DOCUMENT_INTELLIGENCE_SERVICE_URL: str = ""
    DOCUMENT_INTELLIGENCE_AUTH_TOKEN: str = ""
    DOCUMENT_INTELLIGENCE_TIMEOUT_S: int = 90

    # ── Embedding + reranking service ────────────────────────────────────────
    EMBEDDING_RERANKER_SERVICE_URL: str = ""
    EMBEDDING_RERANKER_AUTH_TOKEN: str = ""
    EMBEDDING_RERANKER_TIMEOUT_S: int = 30

    # ── Documents ────────────────────────────────────────────────────────────
    # What an upload may be: read once (substrate.documents.Reader), filed in the conversation's documents.
    RAG_MAX_DOC_PAGES: int = 300
    RAG_MAX_DOC_MB: int = 5
    # After an upload a model reads the document and writes what each section says, a card for the document and (in a knowledge base) where it is
    # filed, so the assistant can choose what to open. It runs in the background; an upload never waits for it and works without it. The model
    # is DOCUMENT_SUMMARY_MODEL, or CHAT_MODEL when that is empty; with no key for it, descriptions are simply not written.
    DOCUMENT_SUMMARY_ENABLED: bool = True
    DOCUMENT_SUMMARY_MODEL: str = ""
    DOCUMENT_SUMMARY_CONCURRENCY: int = 4
    DOCUMENT_SUMMARY_TIMEOUT_S: int = 180
    # Tokens (input + output) one tenant may spend on descriptions per day; 0 is no cap.
    DOCUMENT_SUMMARY_DAILY_TOKENS: int = 5_000_000
    # The knowledge base the chat's `knowledge` tool searches (one per tenant for now; /rag and /internal/knowledge address any by id).
    KNOWLEDGE_CHAT_BASE: str = "default"
    # Email for scheduled-task results (Resend). With no key nothing is sent, and the in-app notification is still made.
    RESEND_API_KEY: str = ""
    NOTIFY_FROM_EMAIL: str = "Assistant <notifications@localhost>"

    # ── Local database paths (PostgreSQL & Redis replacements under DATA_DIR) ──
    HISTORY_STORAGE_PATH: str = ""
    MEMORY_STORAGE_PATH: str = ""
    WORKSPACE_SNAPSHOT_STORAGE_PATH: str = ""

    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    @model_validator(mode="after")
    def _apply_data_dir_defaults(self) -> SubstrateConfig:
        root = self.DATA_DIR.rstrip("/")
        if not self.FILE_STORE_ROOT:
            self.FILE_STORE_ROOT = f"{root}/blobs/{self.FILE_STORE_BUCKET}"
        if not self.HISTORY_STORAGE_PATH:
            self.HISTORY_STORAGE_PATH = f"{root}/db/sessions"
        if not self.MEMORY_STORAGE_PATH:
            self.MEMORY_STORAGE_PATH = f"{root}/db/memory"
        if not self.WORKSPACE_SNAPSHOT_STORAGE_PATH:
            self.WORKSPACE_SNAPSHOT_STORAGE_PATH = f"{root}/db/workspaces"
        if not self.SANDBOX_SCRATCH_ROOT:
            self.SANDBOX_SCRATCH_ROOT = f"{root}/scratch/sandbox"
        if not self.STORE_PATH:
            self.STORE_PATH = f"{root}/store"
        return self

    @property
    def provider_keys(self) -> dict[str, str]:
        """Provider → API-key map for ``create_model_client(..., api_keys=...)``.

        Lets a caller build a client for *any* configured model without knowing
        which provider it routes to — the factory picks the matching key.
        """
        return {
            "openai": self.OPENAI_API_KEY,
            "groq": self.GROQ_API_KEY,
            "anthropic": self.ANTHROPIC_API_KEY,
            "google": self.GEMINI_API_KEY,
            "openrouter": self.OPENROUTER_API_KEY,
            "nvidia": self.NVIDIA_API_KEY,
        }
