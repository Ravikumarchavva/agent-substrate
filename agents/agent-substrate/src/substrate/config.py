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
    # Set only when the K8s agent-sandbox backend is wired to the shared
    # workspace PVC (see capabilities/tools/code_interpreter/code_interpreter/
    # sandbox_service.py::_ensure_user_template). Empty with SANDBOX_RUNTIME=
    # "k8s" means the running code interpreter has no view of uploaded files
    # at all, so chat.py must not tell the model a workspace path is openable
    # (the default "bubblewrap" runtime always has a view — see chat.py's
    # ci_has_workspace_access).
    CI_WORKSPACE_PVC_CLAIM: str = ""
    # ── Sandbox isolation (how agent-generated code is contained) ─────────────
    # "bubblewrap" = Linux namespaces on this host (no daemon, no root, no
    #   nested virtualization). The default: only the caller's own session
    #   directory is mounted, so one user's code cannot see another's files.
    # "k8s"        = one agent-sandbox pod per session (per-user PVC subPath,
    #   optional gVisor RuntimeClass). For cluster deployments.
    # "inprocess"  = NO isolation. Tests/CI only — never multi-user.
    SANDBOX_RUNTIME: str = "bubblewrap"
    # Network reachable from sandboxed code: "deny" | "pip_only" | "full".
    # Deny is the default because the code is LLM-generated and untrusted: with
    # no egress it cannot exfiltrate files even if it reads them.
    SANDBOX_NETWORK_POLICY: str = "deny"
    SANDBOX_TIMEOUT_SECONDS: int = 60
    SANDBOX_MEMORY_BYTES: int = 2 * 1024 * 1024 * 1024
    # Idle sessions whose sandbox is reaped by the janitor (k8s pods; the
    # bubblewrap runtime has no long-lived process to reap).
    SANDBOX_SESSION_TTL_SECONDS: int = 3600
    # Kubernetes RuntimeClass for sandbox pods, e.g. "gvisor". Empty = cluster
    # default (shared host kernel).
    SANDBOX_RUNTIME_CLASS: str = ""
    # Interpreter the bubblewrap runtime executes. Its environment supplies the
    # packages the tool advertises (pandas, matplotlib, …) — install the
    # `sandbox` extra. Empty = the interpreter the engine itself runs under.
    # Point this at a dedicated venv to keep those packages out of the engine's
    # own environment; that venv is mounted read-only into the sandbox.
    SANDBOX_PYTHON: str = ""

    # ── Document-extraction service ──────────────────────────────────────────
    # Optional, isolated microservice for layout-aware document parsing:
    # PaddleOCR layout/chart/table detection + OCR, plus SigLIP multimodal
    # embedding and a MiniLM reranker (see serving/services/extraction/).
    # Empty (the default) means chat attachments fall back to the
    # lightweight pypdf/pdfplumber path for PDFs (no chart images, no
    # multimodal search) — see routes/chat_context.py::_extract_document_text.
    EXTRACTION_SERVICE_URL: str = ""
    EXTRACTION_AUTH_TOKEN: str = ""
    EXTRACTION_TIMEOUT_S: int = 90

    # ── RAG backend ───────────────────────────────────────────────────────────
    # "local" (default) = RAGPipeline + PgVectorStore + extraction-service-or-
    #   pypdf loaders, all self-hosted (see capabilities/knowledge/backends/local.py).
    # "pinecone" = Pinecone Assistant — managed parse+chunk+embed+store+
    #   retrieve, no local processing at all. Requires PINECONE_API_KEY and
    #   PINECONE_ASSISTANT_NAME, and the `rag-pinecone` extra installed.
    # See capabilities/knowledge/backends/factory.py::build_rag_backend.
    RAG_BACKEND: str = "local"
    PINECONE_API_KEY: str = ""
    PINECONE_ASSISTANT_NAME: str = ""
    # Vector dimensionality of EMBEDDING_MODEL's output — must match exactly,
    # since PgVectorStore's `vector({dimensions})` column is fixed per table
    # (CREATE TABLE IF NOT EXISTS never widens/narrows an existing column).
    # Defaults to OpenAI text-embedding-3-small's 1536. Local
    # sentence-transformers models are much smaller: all-MiniLM-L6-v2 (the
    # SentenceTransformersEmbeddingClient default) is 384, all-mpnet-base-v2
    # is 768 — set this to match whichever EMBEDDING_MODEL is configured.
    RAG_TEXT_EMBEDDING_DIM: int = 1536
    # Vector dimensionality of the extraction service's image embedding
    # model — needed for the separate image-vector PgVectorStore table (see
    # backends/local.py's image_store). Qwen3-VL-Embedding-2B (2048) now
    # embeds both text and images into the SAME space (see
    # docs/claude_docs/decisions.md) — this must match EXTRACTION_EMBEDDING_DIM
    # in serving/services/extraction/config.py, or image ingestion hard-fails
    # on a vector-column-width mismatch the first time it actually runs.
    RAG_IMAGE_EMBEDDING_DIM: int = 2048
    # Caps on RAG-eligible document uploads (currently PDF only — see
    # EXTRACTABLE_CONTENT_TYPES in routes/chat_context.py), enforced
    # synchronously at upload time before any storage or extraction cost is
    # spent — routes/files.py::upload_file.
    RAG_MAX_DOC_PAGES: int = 20
    RAG_MAX_DOC_MB: int = 5
    # Daily per-user commit quota: how many documents can actually be *sent*
    # in a chat message (promoted from staging into a real thread
    # collection) — not merely uploaded. See serving/shared/doc_quota.py.
    RAG_DAILY_DOC_LIMIT: int = 20
    # Coarser daily cap on raw upload attempts (separate counter) — eager
    # staging starts unconditionally on upload regardless of the commit
    # quota above, so this bounds worst-case extraction compute from
    # repeated upload-then-discard abuse.
    RAG_DAILY_UPLOAD_ATTEMPT_LIMIT: int = 100
    # Structure-aware chunking (capabilities/knowledge/chunking.py).
    RAG_CHUNK_SIZE: int = 512
    RAG_CHUNK_OVERLAP: int = 128
    # Hybrid retrieval budgets (capabilities/vector/pgvector_store.py::hybrid_search,
    # capabilities/knowledge/reranker.py) — explicit and named rather than
    # inline magic numbers, per the RAG pipeline redesign
    # (docs/claude_docs/decisions.md). Each stage narrows the candidate set:
    # dense/lexical retrieval (50 each) → RRF fusion (50) → a deliberately
    # dumb pre-filter (10) → the expensive multimodal reranker (5 final).
    RAG_DENSE_K: int = 50
    RAG_LEXICAL_K: int = 50
    RAG_FUSED_K: int = 50
    RAG_RERANK_TOP_N: int = 10
    RAG_FINAL_K: int = 5
    # Minimum reranker relevance score to surface a result at all — below
    # this for every candidate means "no confident match," not "force the
    # weakest 5 through anyway."
    RAG_MIN_RERANK_SCORE: float = 0.1

    # ── ONLYOFFICE Document Server (editable Office files in the panel) ───────
    # Empty (default) = no editable Office support; the frontend falls back to
    # the read-only SheetJS/Mammoth preview. Two URLs by design (like
    # CODE_INTERPRETER_URL vs *_EXTERNAL): ONLYOFFICE_URL is the
    # browser-reachable doc-server base (for the editor iframe/API); the doc
    # server reaches OUR backend for document.url / callbackUrl at
    # ONLYOFFICE_INTERNAL_CALLBACK_BASE (e.g. http://host.docker.internal:8000
    # in dev). ONLYOFFICE_JWT_SECRET is the shared secret used to sign the
    # editor config and validate ONLYOFFICE's save callbacks.
    ONLYOFFICE_URL: str = ""
    ONLYOFFICE_INTERNAL_CALLBACK_BASE: str = ""
    ONLYOFFICE_JWT_SECRET: str = ""

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
