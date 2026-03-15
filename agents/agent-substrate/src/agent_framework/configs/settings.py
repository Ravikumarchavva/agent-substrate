from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path


class Settings(BaseSettings):

    ROOT_DIR: Path = Path(__file__).parent.parent.parent.parent
    OPENAI_API_KEY: str
    DATABASE_URL: str

    # Redis (short-term memory)
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_SESSION_TTL: int = 3600  # seconds (1 hour default)

    # Session management
    SESSION_MAX_MESSAGES: int = 200
    SESSION_AUTO_CHECKPOINT: int = 50  # flush to Postgres every N messages (0 = off)

    # Model context window — how many messages (non-system) to include in each
    # LLM call.  System message is always prepended.  Older messages stay in
    # Redis (full history) but are excluded from the context sent to the model.
    # Tune this to balance cost vs. context quality.
    MODEL_CONTEXT_WINDOW: int = 40

    # Spotify API credentials
    SPOTIFY_CLIENT_ID: str = ""
    SPOTIFY_CLIENT_SECRET: str = ""
    SPOTIFY_REDIRECT_URI: str = ""  # OAuth callback URL (default: http://localhost:8001/auth/spotify/callback)

    # Frontend URL — used by tools that need to call back into the Next.js API
    # (e.g. SpotifyPlayerTool fetching the OAuth token endpoint).
    FRONTEND_URL: str = "http://127.0.0.1:3000"

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