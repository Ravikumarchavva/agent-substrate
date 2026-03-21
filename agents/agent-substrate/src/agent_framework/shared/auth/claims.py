"""Auth claims model shared by all services.

Every API and event must carry tenant_id, workspace_id, actor_id, and role claims
(per docs/microservices/02-role-and-responsibility-matrix.md).
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class AuthClaims(BaseModel):
    """Decoded identity claims available to every service handler.

    Populated from JWT access token or service-runtime token. All services
    must propagate these claims on every outgoing request or event.
    """

    sub: str                        # stable user/service identity
    email: str = ""
    role: str = "end_user"          # platform role
    tenant_id: str = "default"
    workspace_id: str = "default"
    jti: str = ""
    token_type: str = "access"      # access | refresh | agent | service
    # Agent context fields (optional)
    thread_id: Optional[str] = None
    permissions: Optional[list[str]] = None

    @property
    def is_admin(self) -> bool:
        return self.role in ("platform_admin", "tenant_admin")

    @property
    def is_service(self) -> bool:
        return self.token_type == "service"
