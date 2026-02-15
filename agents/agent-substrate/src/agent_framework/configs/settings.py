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

    # Spotify API credentials
    SPOTIFY_CLIENT_ID: str = ""
    SPOTIFY_CLIENT_SECRET: str = ""
    SPOTIFY_REDIRECT_URI: str = ""  # OAuth callback URL (default: http://localhost:8001/auth/spotify/callback)

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