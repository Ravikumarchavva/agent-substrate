from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path
from typing import List, Optional


class Settings(BaseSettings):
    ROOT_DIR: Path = Path(__file__).parent.parent.parent.parent
    OPENAI_API_KEY: str = ""
    DATABASE_URL: str = ""

    # Redis (short-term memory)
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_SESSION_TTL: int = 3600  # seconds (1 hour default)

    # Session management
    SESSION_MAX_MESSAGES: int = 200
    SESSION_AUTO_CHECKPOINT: int = 50  # flush to Postgres every N messages (0 = off)

    # LLM models
    # Override these in .env to switch globally, or let the frontend per-request
    # override take precedence (Settings → General → Model).
    CHAT_MODEL: str = "gpt-5.4-mini"
    STT_MODEL: str = "whisper-1"

    # Model context window — how many messages (non-system) to include in each
    # LLM call.  System message is always prepended.  Older messages stay in
    # Redis (full history) but are excluded from the context sent to the model.
    # Tune this to balance cost vs. context quality.
    MODEL_CONTEXT_WINDOW: int = 40

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
    # In production set this to your exact frontend domain(s), e.g.:
    # CORS_ALLOWED_ORIGINS=https://app.example.com,https://www.example.com
    CORS_ALLOWED_ORIGINS: List[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:3002",
        "http://127.0.0.1:3002",
    ]

    # OpenTelemetry — OTLP HTTP endpoint for traces.
    # Set to "" to disable tracing entirely.
    OTLP_ENDPOINT: str = "http://localhost:4318"

    # Visual Builder — set to True to mount /builder API routes.
    # Keep False in production to avoid bloat.
    ENABLE_BUILDER: bool = False

    # JWT authentication
    # JWT_SECRET must be set to a 32+ char random string in production.
    # Generate with: python -c "import secrets; print(secrets.token_hex(32))"
    JWT_SECRET: str = "CHANGE_ME_IN_PRODUCTION_USE_A_STRONG_RANDOM_SECRET"
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

    # ── Distributed Runtime (Restate + NATS) ─────────────────────────────
    RESTATE_INGRESS_URL: str = "http://localhost:8080"
    RESTATE_ADMIN_URL: str = "http://localhost:9070"
    NATS_URL: str = "nats://localhost:4222"

    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )


settings = Settings()
if __name__ == "__main__":
    settings = Settings()
    print(settings.model_dump_json())
