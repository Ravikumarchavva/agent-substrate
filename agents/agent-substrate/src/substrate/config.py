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

from pydantic_settings import BaseSettings, SettingsConfigDict


class SubstrateConfig(BaseSettings):
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
    TTS_MODEL: str = "google/gemini-3.1-flash-tts-preview"
    TTS_VOICE: str = "Kore"
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
    # "local" (WorkspaceFileStore) | "s3" (SeaweedFS/S3) | "memory" (tests)
    FILE_STORE_BACKEND: str = "local"
    FILE_STORE_ROOT: str = "./data/workspaces"
    FILE_STORE_BUCKET: str = "agent-files"
    FILE_STORE_ENDPOINT: str | None = None
    FILE_STORE_REGION: str = "us-east-1"
    FILE_STORE_ACCESS_KEY: str | None = None
    FILE_STORE_SECRET_KEY: str | None = None
    FILE_STORE_PREFIX: str = ""
    FILE_ENCRYPTION_MODE: str = "none"
    FILE_KEK_HEX: str = ""
    FILE_MAX_UPLOAD_BYTES: int = 200 * 1024 * 1024

    # ── Workspace quotas ─────────────────────────────────────────────────────
    WORKSPACE_USER_QUOTA_BYTES: int = 1024 * 1024 * 1024
    WORKSPACE_USER_DELETE_ALLOWED: bool = True

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
    RAG_BACKEND: str = "local"  # "local" | "pinecone"
    PINECONE_API_KEY: str = ""
    PINECONE_ASSISTANT_NAME: str = ""
    RAG_TEXT_EMBEDDING_DIM: int = 1536
    RAG_IMAGE_EMBEDDING_DIM: int = 2048
    RAG_MAX_DOC_PAGES: int = 20
    RAG_MAX_DOC_MB: int = 5
    RAG_DAILY_DOC_LIMIT: int = 20
    RAG_DAILY_UPLOAD_ATTEMPT_LIMIT: int = 100
    RAG_CHUNK_SIZE: int = 512
    RAG_CHUNK_OVERLAP: int = 128
    RAG_DENSE_K: int = 50
    RAG_LEXICAL_K: int = 50
    RAG_FUSED_K: int = 50
    RAG_RERANK_TOP_N: int = 10
    RAG_FINAL_K: int = 5
    RAG_MIN_RERANK_SCORE: float = 0.1

    FRONTEND_URL: str = "http://127.0.0.1:3000"

    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

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
