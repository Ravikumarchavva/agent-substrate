from __future__ import annotations

from pathlib import Path

from ravi.config import Settings


def test_settings_accepts_common_provider_env_aliases(tmp_path: Path, monkeypatch) -> None:
    # Clear any real env vars so they don't shadow the test .env file.
    for key in ("OPENROUTER_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        (
            "OPENROUTER_API_KEY=router-key\n"
            "GEMINI_API_KEY=gemini-key\n"
            "GROQ_API_KEY=groq-key\n"
            "JWT_SECRET=test-secret-for-unit-tests-that-is-long-enough-32c\n"
        ),
        encoding="utf-8",
    )

    configured = Settings(_env_file=env_file)

    assert configured.OPENROUTER_API_KEY == "router-key"
    assert configured.GEMINI_API_KEY == "gemini-key"
    assert configured.GROQ_API_KEY == "groq-key"
