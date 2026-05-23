from __future__ import annotations

from pathlib import Path

from ravi.configs.settings import Settings
from ravi.core.agents.default_agent import Agent


def test_settings_accepts_common_provider_env_aliases(tmp_path: Path) -> None:
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


def test_agent_resolves_common_provider_env_aliases(
    monkeypatch,
) -> None:
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "router-key")
    monkeypatch.setenv("GROK_API_KEY", "groq-key")

    openrouter_keys = Agent._resolve_api_keys(
        "openrouter/liquid/lfm-2.5-1.2b-thinking:free"
    )
    google_keys = Agent._resolve_api_keys("google/gemini-2.5-flash")
    groq_keys = Agent._resolve_api_keys("groq/llama-3.3-70b-versatile")

    assert openrouter_keys["openrouter"] == "router-key"
    assert google_keys["google"] == "gemini-key"
    assert groq_keys["groq"] == "groq-key"
