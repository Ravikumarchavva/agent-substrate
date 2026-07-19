"""substrate library configuration.

``SubstrateConfig`` holds everything the library layer needs — API keys, model
defaults, storage URLs, and tool settings.  It reads from environment variables
only (no ``.env`` file loading).  Consumers instantiate it explicitly:

    cfg = SubstrateConfig(openai_api_key="sk-...")
    runtime = Runtime(config=cfg)

If you are running the built-in FastAPI server, use
``substrate.serving.shared.settings.ServerSettings`` instead — it extends this
class and adds server-only fields (JWT, CORS, rate limits, observability) with
``.env`` file auto-loading.
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

    # ── Database ─────────────────────────────────────────────────────────────
    DATABASE_URL: str = ""
    ASYNC_DATABASE_URL: str = ""

    # ── Durable runtime asyncpg pool ─────────────────────────────────────────
    # This is a SEPARATE pool from the ORM's own (SQLAlchemy/asyncpg or
    # psycopg engine) — see build_postgres_runtime(). Total connection budget
    # against your Postgres max_connections is (this pool) + (ORM engine pool)
    # per process, times the number of replicas.
    RUNTIME_PG_POOL_MIN_SIZE: int = 2
    RUNTIME_PG_POOL_MAX_SIZE: int = 10

    # ── Redis ────────────────────────────────────────────────────────────────
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_SESSION_TTL: int = 3600

    # ── Agent runtime backend ────────────────────────────────────────────────
    # "postgres" — durable (EventLogProtocol/InboxProtocol/SchedulerProtocol/SignalBusProtocol/SupervisorProtocol in Postgres)
    # "memory"   — in-process, no durability; lighter for dev / tests
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
    # PDFs are extracted server-side (pypdf/pdfplumber) and inlined into the
    # prompt like text/* attachments, capped so one large PDF can't blow the
    # context window. See routes/chat_context.py::_build_file_context.
    ATTACHMENT_PDF_MAX_CHARS: int = 20000

    # ── Tool behaviour ───────────────────────────────────────────────────────
    DISABLE_TOOL_APPROVALS: bool = False

    # ── File storage ─────────────────────────────────────────────────────────
    # "local" (default) = WorkspaceFileStore, a per-user directory tree on
    # server-side storage (local dir in dev, docker volume in compose, RWX PVC
    # in k8s — see capabilities/storage/workspace.py). "s3" = MinIO/S3,
    # opt-in. "memory" = InMemoryFileStore, tests only.
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

    # ── Workspace (per-user filesystem: uploads + code-interpreter workdir) ───
    WORKSPACE_USER_QUOTA_BYTES: int = 1024 * 1024 * 1024
    WORKSPACE_USER_DELETE_ALLOWED: bool = True

    # ── Code interpreter sandbox ─────────────────────────────────────────────
    # LocalSandboxCodeInterpreterTool — a locally docker-composed sandbox
    # container (see deployment/docker/docker-compose.yml, `--profile
    # sandbox`), talked to over plain HTTP. Empty (the default) means no
    # local-dev code interpreter is registered unless the K8s agent-sandbox
    # backend is available.
    CI_LOCAL_SANDBOX_URL: str = ""
    # Set only when the K8s agent-sandbox backend is wired to the shared
    # workspace PVC (see capabilities/tools/code_interpreter/code_interpreter/
    # sandbox_service.py::_ensure_user_template), or when
    # CI_LOCAL_SANDBOX_URL's bind-mounted workspace is configured. Empty
    # (the default) means the running code interpreter has no view of
    # uploaded files at all, so chat.py must not tell the model a workspace
    # path is openable.
    CI_WORKSPACE_PVC_CLAIM: str = ""

    # ── Docling extraction service ───────────────────────────────────────────
    # Optional, isolated microservice for structure-aware document parsing
    # (see serving/services/docling/). Empty (the default) means chat
    # attachments fall back to the lightweight pypdf/pdfplumber path for
    # PDFs, and DOCX/PPTX stay metadata-only — see
    # routes/chat_context.py::_extract_document_text.
    DOCLING_SERVICE_URL: str = ""
    DOCLING_AUTH_TOKEN: str = ""
    DOCLING_TIMEOUT_S: int = 90

    FRONTEND_URL: str = "http://127.0.0.1:3000"

    model_config = SettingsConfigDict(
        # No env_file — reads from environment variables only.
        # The server layer (ServerSettings) adds env_file loading on top.
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
