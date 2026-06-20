from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    ROOT_DIR: Path = Path(__file__).parent.parent.parent
    OPENAI_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""
    GEMINI_API_KEY: str = ""
    GROQ_API_KEY: str = ""
    OPENAI_BASE_URL: str = ""
    GROQ_BASE_URL: str = "https://api.groq.com/openai/v1"
    OPENROUTER_API_KEY: str = ""
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
    NVIDIA_API_KEY: str = ""
    OPENROUTER_SITE_URL: str = "http://localhost:3000"
    OPENROUTER_APP_NAME: str = "Ravi UI"
    ASYNC_DATABASE_URL: str = ""
    DATABASE_URL: str = ""

    # Redis (short-term memory)
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_SESSION_TTL: int = 3600  # seconds (1 hour default)

    # Agent runtime backend: "postgres" (durable, default) or "memory".
    # postgres → EventLog/Inbox/Scheduler in Postgres + Redis effect journal;
    # runs and their event logs survive restarts and are readable by any worker.
    # memory → in-process, no durability; an opt-out for lightweight dev runs
    # that don't need run survival (lighter: no Postgres tail-polling).
    RUNTIME_BACKEND: str = "postgres"

    # Session management
    SESSION_MAX_MESSAGES: int = 200
    SESSION_AUTO_CHECKPOINT: int = 50  # flush to Postgres every N messages (0 = off)

    # LLM models
    # Override these in .env to switch globally, or let the frontend per-request
    # override take precedence (Settings → General → Model).
    AGENT_MODE: str = "react"  # "react" | "orchestrator"
    CHAT_MODEL: str = "google/gemini-3.1-flash-lite"
    EMBEDDING_MODEL: str = "text-embedding-3-small"
    STT_MODEL: str = "whisper-1"
    TTS_MODEL: str = "google/gemini-3.1-flash-tts-preview"
    TTS_VOICE: str = "Kore"
    REALTIME_MODEL: str = "gpt-4o-realtime-preview-2024-12-17"
    REALTIME_VOICE: str = "coral"

    # Model context window — how many messages (non-system) to include in each
    # LLM call.  System message is always prepended.  Older messages stay in
    # Redis (full history) but are excluded from the context sent to the model.
    # Tune this to balance cost vs. context quality.
    MODEL_CONTEXT_WINDOW: int = 40

    # Semantic cache — embedding-based response caching.
    # When enabled, LLM responses are cached by query similarity.
    SEMANTIC_CACHE_ENABLED: bool = False
    SEMANTIC_CACHE_THRESHOLD: float = 0.95
    SEMANTIC_CACHE_TTL: int = 3600

    # Web search providers (Exa → Tavily → DuckDuckGo, first key found wins)
    EXA_API_KEY: str = ""
    TAVILY_API_KEY: str = ""
    WEB_SEARCH_MAX_RESULTS: int = 3
    WEB_SEARCH_MAX_CHARS: int = 5000
    WEB_READ_MAX_CHARS: int = 6000

    # Spotify API credentials
    SPOTIFY_CLIENT_ID: str = ""
    SPOTIFY_CLIENT_SECRET: str = ""
    SPOTIFY_REDIRECT_URI: str = (
        ""  # OAuth callback URL (default: http://localhost:8001/auth/spotify/callback)
    )

    # Frontend URL — used by tools that need to call back into the Next.js API
    # (e.g. SpotifyPlayerTool fetching the OAuth token endpoint).
    FRONTEND_URL: str = "http://127.0.0.1:3000"

    # CORS — comma-separated list of allowed origins.
    # Portfolio frontend on Vercel + local dev defaults:
    # CORS_ALLOWED_ORIGINS=https://your-portfolio.vercel.app,https://localhost:3000
    CORS_ALLOWED_ORIGINS: List[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3001",
        "http://localhost:3002",
        "http://127.0.0.1:3002",
    ]

    # OpenTelemetry — OTLP HTTP endpoint for traces.
    # Set to "" to disable tracing entirely.
    OTLP_ENDPOINT: str = "http://localhost:4318"

    # Visual Builder — set to True to mount /builder API routes.
    # Keep False in production to avoid bloat.
    ENABLE_BUILDER: bool = False

    # Disable high-risk tool approvals in local dev
    DISABLE_TOOL_APPROVALS: bool = False

    # Portfolio chatbot — public endpoint rate limit (requests per minute per IP).
    # Cached responses do NOT count against this limit (no LLM cost).
    PORTFOLIO_RATE_LIMIT_RPM: int = 10

    # HTTP rate limiting (sliding window, Redis-backed).
    # RATE_LIMIT_ENABLED=false disables all rate limiting (useful in dev/CI).
    # RATE_LIMIT_AUTHED_RPM — max requests per minute per authenticated user_id.
    # RATE_LIMIT_ANON_RPM  — max requests per minute per IP for anonymous callers.
    # RATE_LIMIT_WINDOW_SECONDS — window size; change to 3600 for per-hour limits.
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_AUTHED_RPM: int = 60
    RATE_LIMIT_ANON_RPM: int = 5
    RATE_LIMIT_WINDOW_SECONDS: int = 86400  # 24 hours — 5 messages per day for anon

    # Kubernetes Sandbox Code Interpreter Settings
    CODE_INTERPRETER_URL: str = ""
    CI_NAMESPACE: str = "agent-framework"
    CI_HEADLESS_SERVICE: str = ""
    CI_REPLICAS: int = 1

    # JWT authentication
    # JWT_SECRET must be set to a 32+ char random string in production.
    # Generate with: python -c "import secrets; print(secrets.token_hex(32))"
    JWT_SECRET: str = "CHANGE_ME_IN_PRODUCTION_USE_A_STRONG_RANDOM_SECRET"

    @field_validator("JWT_SECRET")
    @classmethod
    def validate_jwt_secret(cls, v: str) -> str:
        if v == "CHANGE_ME_IN_PRODUCTION_USE_A_STRONG_RANDOM_SECRET" or len(v) < 32:
            raise ValueError(
                "JWT_SECRET must be set to a strong random secret (min 32 chars). "
                "Generate one with: openssl rand -hex 32"
            )
        return v

    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 30
    # Agent context tokens are short-lived ephemeral tokens bound to a thread
    JWT_AGENT_TOKEN_EXPIRE_MINUTES: int = 5

    # ── File Storage ─────────────────────────────────────────────────────
    # Backend driver: "local", "s3"
    FILE_STORE_BACKEND: str = "local"

    # Local driver — base directory for file storage
    FILE_STORE_ROOT: str = ""

    # S3-compatible driver (AWS S3, MinIO, R2, Spaces)
    FILE_STORE_BUCKET: str = "agent-files"
    FILE_STORE_ENDPOINT: Optional[str] = None
    FILE_STORE_REGION: str = "us-east-1"
    FILE_STORE_ACCESS_KEY: Optional[str] = None
    FILE_STORE_SECRET_KEY: Optional[str] = None
    FILE_STORE_PREFIX: str = ""

    # Encryption: "none", "envelope"
    FILE_ENCRYPTION_MODE: str = "none"
    # 64-char hex key for local KEK (dev only, used when FILE_KEK_PROVIDER=local)
    FILE_KEK_HEX: str = ""
    # Max upload size in bytes (default 200 MB)
    FILE_MAX_UPLOAD_BYTES: int = 200 * 1024 * 1024

    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )


settings = Settings()
