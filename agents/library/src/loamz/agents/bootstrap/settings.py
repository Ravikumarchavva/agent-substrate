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


class HttpSettings(BaseModel):
    cors_origins: list[str] = Field(default_factory=list)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="LOAMZ_AGENTS_", env_nested_delimiter="__", env_file=".env", extra="ignore"
    )

    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    oidc: OidcSettings = Field(default_factory=OidcSettings)
    http: HttpSettings = Field(default_factory=HttpSettings)
    roles: list[str] = Field(
        default_factory=lambda: ["all"],
        description="Which feature roles this process serves (`all`, or a subset: microservices mode).",
    )
