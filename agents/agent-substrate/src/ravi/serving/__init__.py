"""ravi.serving — deployment shells for the agent framework.

Three sub-packages:
  monolith/   — single FastAPI application
  services/   — 12 independent microservices
  shared/     — cross-service auth, database, events, observability
"""

from __future__ import annotations
