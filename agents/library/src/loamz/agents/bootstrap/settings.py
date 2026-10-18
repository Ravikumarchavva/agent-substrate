"""Settings: the only place configuration is read. One typed tree, environment variables `LOAMZ_AGENTS_<SECTION>__<NAME>`.

    LOAMZ_AGENTS_DATABASE__URL=postgresql://…      (unset: SQLite in LOAMZ_AGENTS_DATABASE__PATH)
    LOAMZ_AGENTS_OIDC__ISSUER=https://id.example.com/realms/loamz

`.env.example`, the compose environment and the Helm values schema are generated from this model
(`loamz-agents settings export`), so they cannot drift from what the code reads.
"""

from __future__ import annotations

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseModel):
    url: str = Field(
        default="", description="PostgreSQL DSN. Empty: an embedded SQLite file at `path`."
    )
    path: str = Field(
        default="./.loamz/agents.db", description="SQLite file used when `url` is empty."
    )
    schema_name: str = Field(
        default="loamz_agents", description="PostgreSQL schema holding the agent tables."
    )
    pool_min: int = Field(default=2, ge=1)
    pool_max: int = Field(default=10, ge=1)


class OidcSettings(BaseModel):
    issuer: str = Field(
        default="", description="Keycloak realm issuer; empty disables sign-in (local use only)."
    )
    audience: str = Field(default="loamz-agents")
    insecure_http: bool = Field(
        default=False, description="Allow plain http to the issuer (a private network)."
    )


class LlmSettings(BaseModel):
    model: str = Field(
        default="gpt-4o-mini",
        description="Default chat model, `provider/model` or a bare name the provider is detected from.",
    )
    embedding_model: str = Field(default="text-embedding-3-small")
    openai_api_key: str = Field(default="", description="Also used by OpenAI-compatible servers.")
    openai_base_url: str = Field(
        default="", description="An OpenAI-compatible endpoint (vLLM, Ollama…)."
    )
    anthropic_api_key: str = ""
    gemini_api_key: str = ""
    groq_api_key: str = ""
    openrouter_api_key: str = ""
    nvidia_api_key: str = ""

    def api_keys(self) -> dict[str, str]:
        """Provider name -> key, as `providers.llm.create_model_client(..., api_keys=…)` takes them."""
        return {
            "openai": self.openai_api_key,
            "anthropic": self.anthropic_api_key,
            "google": self.gemini_api_key,
            "groq": self.groq_api_key,
            "openrouter": self.openrouter_api_key,
            "nvidia": self.nvidia_api_key,
        }


class RedisSettings(BaseModel):
    url: str = Field(
        default="", description="Redis for the event bus between roles; empty: an in-process bus."
    )


class TelemetrySettings(BaseModel):
    capture_content: bool = Field(
        default=False,
        description="Put prompt, response and tool text on spans (telemetry is not erased with a tenant).",
    )


class HttpSettings(BaseModel):
    cors_origins: list[str] = Field(default_factory=list)
    frontend_url: str = Field(
        default="http://127.0.0.1:3000",
        description="Where links in notifications and e-mails point.",
    )


class FilesSettings(BaseModel):
    path: str = Field(
        default="./.loamz/files",
        description="Folder holding file bytes when no data service is configured.",
    )
    max_upload_bytes: int = Field(default=200 * 1024 * 1024, ge=1)
    user_quota_bytes: int = Field(default=1024**3, ge=0, description="Per user; 0 is unlimited.")
    user_delete_allowed: bool = True
    staging_ttl_minutes: float = Field(
        default=10.0,
        description="An upload whose staging started but never finished is staged again once this old.",
    )


class ChatSettings(BaseModel):
    context_window: int = Field(
        default=40, ge=1, description="Messages of history the model is shown."
    )
    max_iterations: int = Field(default=30, ge=1)
    max_concurrent_runs: int = Field(
        default=10, ge=1, description="Runs one worker executes at once."
    )
    disable_tool_approvals: bool = False
    attachment_pdf_max_chars: int = 20000
    knowledge_base: str = Field(
        default="default", description="The knowledge base the chat `knowledge` tool searches."
    )
    group_triage_model: str = Field(
        default="",
        description="Small model an agent in a group uses for its quick look at a message not for it; empty: the chat model.",
    )


class AudioSettings(BaseModel):
    stt_model: str = "whisper-1"
    tts_model: str = "local/kokoro-82m"
    tts_voice: str = "Kore"
    realtime_model: str = "gpt-4o-realtime-preview-2024-12-17"
    realtime_voice: str = "coral"


class DocumentSettings(BaseModel):
    max_pages: int = 300
    max_mb: int = 5
    summary_enabled: bool = True
    summary_model: str = Field(default="", description="Empty: the chat model.")
    summary_concurrency: int = 4
    summary_timeout_s: int = 180
    summary_daily_tokens: int = Field(
        default=5_000_000, description="Per tenant per day; 0 is no cap."
    )
    daily_doc_limit: int = 20
    daily_upload_attempt_limit: int = 100


class NotificationSettings(BaseModel):
    resend_api_key: str = Field(
        default="", description="With no key nothing is e-mailed; the in-app notice is still made."
    )
    from_email: str = "Assistant <notifications@localhost>"


class SchedulerSettings(BaseModel):
    catch_up: bool = Field(
        default=True,
        description="On start, run once each scheduled task whose firing came due while the server was down.",
    )


class RateLimitSettings(BaseModel):
    enabled: bool = True
    authed_rpm: int = 60
    anon_rpm: int = 5
    window_seconds: int = 60
    fail_open: bool = Field(
        default=False, description="Allow traffic when the counters are unavailable (dev only)."
    )


class SafetySettings(BaseModel):
    text_guard: bool = True
    text_threshold: float = 0.9
    image_nsfw_threshold: float = 0.5
    image_nsfl_threshold: float = 0.3


class SandboxSettings(BaseModel):
    runtime: str = Field(default="nsjail", description="nsjail | k8s | inprocess (tests only)")
    network_policy: str = "deny"
    timeout_seconds: int = 60
    memory_bytes: int = 2 * 1024**3
    scratch_root: str = ""
    python: str = ""
    runtime_class: str = ""
    workspace_pvc_claim: str = ""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="LOAMZ_AGENTS_", env_nested_delimiter="__", env_file=".env", extra="ignore"
    )

    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    oidc: OidcSettings = Field(default_factory=OidcSettings)
    http: HttpSettings = Field(default_factory=HttpSettings)
    llm: LlmSettings = Field(default_factory=LlmSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    files: FilesSettings = Field(default_factory=FilesSettings)
    chat: ChatSettings = Field(default_factory=ChatSettings)
    audio: AudioSettings = Field(default_factory=AudioSettings)
    documents: DocumentSettings = Field(default_factory=DocumentSettings)
    notifications: NotificationSettings = Field(default_factory=NotificationSettings)
    scheduler: SchedulerSettings = Field(default_factory=SchedulerSettings)
    rate_limit: RateLimitSettings = Field(default_factory=RateLimitSettings)
    safety: SafetySettings = Field(default_factory=SafetySettings)
    sandbox: SandboxSettings = Field(default_factory=SandboxSettings)
    telemetry: TelemetrySettings = Field(default_factory=TelemetrySettings)
    roles: list[str] = Field(
        default_factory=lambda: ["all"],
        description="Which feature roles this process serves (`all`, or a subset: microservices mode).",
    )
