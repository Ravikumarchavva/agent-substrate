from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path


class Settings(BaseSettings):

    ROOT_DIR: Path = Path(__file__).parent.parent.parent.parent
    OPENAI_API_KEY: str

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