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

from typing import Optional

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

    # ── Redis ────────────────────────────────────────────────────────────────
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_SESSION_TTL: int = 3600

    # ── Agent runtime backend ────────────────────────────────────────────────
    # "postgres" — durable (EventLog/Inbox/Scheduler in Postgres + Redis journal)
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

    # ── Tool behaviour ───────────────────────────────────────────────────────
    DISABLE_TOOL_APPROVALS: bool = False

    # ── File storage ─────────────────────────────────────────────────────────
    FILE_STORE_BACKEND: str = "local"
    FILE_STORE_ROOT: str = ""
    FILE_STORE_BUCKET: str = "agent-files"
    FILE_STORE_ENDPOINT: Optional[str] = None
    FILE_STORE_REGION: str = "us-east-1"
    FILE_STORE_ACCESS_KEY: Optional[str] = None
    FILE_STORE_SECRET_KEY: Optional[str] = None
    FILE_STORE_PREFIX: str = ""
    FILE_ENCRYPTION_MODE: str = "none"
    FILE_KEK_HEX: str = ""
    FILE_MAX_UPLOAD_BYTES: int = 200 * 1024 * 1024

    # ── Code interpreter sandbox ─────────────────────────────────────────────
    CODE_INTERPRETER_URL: str = ""
    CI_NAMESPACE: str = "agent-framework"
    CI_HEADLESS_SERVICE: str = ""
    CI_REPLICAS: int = 1

    # ── Third-party connectors ───────────────────────────────────────────────
    SPOTIFY_CLIENT_ID: str = ""
    SPOTIFY_CLIENT_SECRET: str = ""
    SPOTIFY_REDIRECT_URI: str = ""

    FRONTEND_URL: str = "http://127.0.0.1:3000"

    model_config = SettingsConfigDict(
        # No env_file — reads from environment variables only.
        # The server layer (ServerSettings) adds env_file loading on top.
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )
