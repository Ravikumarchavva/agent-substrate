from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from loamz.core import tenancy


class TenantEnsure(BaseModel):
    """The tenant (a Keycloak organization id). Its alias and every name are derived from it (`tenancy`)."""

    model_config = ConfigDict(extra="forbid")
    tenant_id: Annotated[str, Field(pattern=tenancy.TENANT_ID_PATTERN)]
    alias: str | None = None


class Tenant(BaseModel):
    tenant_id: str
    alias: str
    catalog: str
    schemas: list[str]
    volumes: list[str]
    group: str
    created: bool
