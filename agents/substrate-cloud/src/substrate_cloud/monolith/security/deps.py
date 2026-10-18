"""The monolith's authentication dependencies are the shared ones (``shared.auth.middleware``)."""

from __future__ import annotations

from substrate_cloud.shared.auth.claims import AuthClaims  # noqa: F401
from substrate_cloud.shared.auth.middleware import (  # noqa: F401
    get_current_user,
    optional_current_user,
)
