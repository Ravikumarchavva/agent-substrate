"""FastAPI authentication dependencies — delegates to shared.auth.middleware.

The monolith versions bind ``settings.JWT_SECRET`` automatically (the shared
middleware reads it from ``app.state.jwt_secret`` which is set in the monolith
lifespan).  Import from here for monolith routes; microservices import from
``shared.auth.middleware`` directly.
"""

from __future__ import annotations

# Re-export so existing `from server.security.deps import get_current_user` works
from agent_framework.shared.auth.middleware import (  # noqa: F401
    get_current_user,
    optional_current_user,
)

# Re-export alias for annotation convenience
from agent_framework.server.security.jwt import TokenPayload  # noqa: F401
