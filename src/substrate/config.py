"""Substrate library configuration.

``SubstrateConfig`` holds everything the library layer needs — API keys, model
defaults, storage URLs, and tool settings. It reads from environment variables
only (no ``.env`` file loading). Consumers instantiate it explicitly:

    cfg = SubstrateConfig(openai_api_key="sk-...")
    runtime = Runtime(config=cfg)

For running the built-in FastAPI server, use
``substrate.serving.shared.settings.ServerSettings`` instead — it extends this
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

    # ── Durable runtime asyncpg pool ─────────────────────────────────────────
    RUNTIME_PG_POOL_MIN_SIZE: int = 2
    RUNTIME_PG_POOL_MAX_SIZE: int = 10

    # ── Redis ────────────────────────────────────────────────────────────────
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_SESSION_TTL: int = 3600

    # ── Agent runtime backend ────────────────────────────────────────────────
    # "postgres" (durable) | "memory" (ephemeral/tests)
    RUNTIME_BACKEND: str = "postgres"

    # ── Session / context ────────────────────────────────────────────────────
    SESSION_MAX_MESSAGES: int = 200
    SESSION_AUTO_CHECKPOINT: int = 50

    # ── Model defaults ───────────────────────────────────────────────────────
    AGENT_MODE: str = "react"  # "react" | "orchestrator"
    CHAT_MODEL: str = "google/gemini-3.1-flash-lite"
    EMBEDDING_MODEL: str = "text-embedding-3-small"
    STT_MODEL: str = "whisper-1"
    TTS_MODEL: str = "local/kokoro-82m"
    TTS_VOICE: str = "af_heart"
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
    # "local" (WorkspaceFileStore) | "s3" (SeaweedFS/S3)
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

    # ── RAG backend & retrieval ──────────────────────────────────────────────
    # "local" (LocalRagBackend, PgVectorStore) is the only real backend --
    # Pinecone support was removed as dead weight (never the standard
    # path); the per-user session-document index already uses LanceDB as
    # this project's own self-hosted alternative where a second backend
    # was actually needed (capabilities/vector/lancedb_store.py).
    RAG_BACKEND: str = "local"
    RAG_TEXT_EMBEDDING_DIM: int = 1536
    RAG_IMAGE_EMBEDDING_DIM: int = 2048
    RAG_MAX_DOC_PAGES: int = 300
    RAG_MAX_DOC_MB: int = 5
    # None (default) means "let capabilities/knowledge/chunking.py's
    # recommend_chunk_params(EMBEDDING_MODEL) pick a size informed by the
    # configured embedding model's real max input token limit" -- an
    # explicit value here always wins over that (same explicit-always-wins
    # precedence document_intelligence's autoconfig.py already uses).
    RAG_CHUNK_SIZE: int | None = None
    RAG_CHUNK_OVERLAP: int | None = None
    RAG_DENSE_K: int = 50
    RAG_LEXICAL_K: int = 50
    RAG_FUSED_K: int = 50
    RAG_RERANK_TOP_N: int = 10
    RAG_FINAL_K: int = 5
    RAG_MIN_RERANK_SCORE: float = 0.1

    # ── Session document index (per-user vector/tree/graph — LanceDB) ─────────
    # Empty (default): the vector/pageindex/graph store use a local, embedded
    # LanceDB directory under SESSION_INDEX_LOCAL_PATH — no server needed.
    # Set SESSION_INDEX_NAMESPACE_URI to point at a Lance Namespace REST
    # catalog instead (e.g. SeaweedFS's Lance Catalog,
    # `weed server -s3.port.lance=9101`) — see
    # capabilities/vector/lancedb_store.py's module docstring for what was
    # verified about that mode (namespace_path shape, credential handling).
    SESSION_INDEX_LOCAL_PATH: str = ""
    SESSION_INDEX_NAMESPACE_URI: str = ""
    SESSION_INDEX_BUCKET: str = "agent-files"

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
        if not self.SESSION_INDEX_LOCAL_PATH:
            self.SESSION_INDEX_LOCAL_PATH = f"{root}/blobs/{self.FILE_STORE_BUCKET}"
        if not self.HISTORY_STORAGE_PATH:
            self.HISTORY_STORAGE_PATH = f"{root}/db/sessions"
        if not self.MEMORY_STORAGE_PATH:
            self.MEMORY_STORAGE_PATH = f"{root}/db/memory"
        if not self.WORKSPACE_SNAPSHOT_STORAGE_PATH:
            self.WORKSPACE_SNAPSHOT_STORAGE_PATH = f"{root}/db/workspaces"
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
