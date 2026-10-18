# Microservices

11 independent FastAPI applications, one per folder.

Each service follows a standard layout (`app.py`, `routes.py`, `service.py`, `models.py`)
except `gateway` (BFF proxy), `live_stream` (SSE projector), and `tool_executor` (executor pattern)
which intentionally omit `models.py`/`service.py`.

Service-to-service communication uses Pydantic DTOs from `shared/contracts/`
and factory-built events from `shared/events/types.py`.

See `CLAUDE.md` → "Microservices — Roles & ORM Models" for the full table.

## Authentication

Every router of every service is included with `dependencies=AUTHENTICATED` (`services/base.py`): the bearer token is a Keycloak access
token verified by `substrate-identity` (RS256, audience `agent-substrate`, one organization, see ADR 0007). The gateway forwards the
caller's own token downstream. Only `/health` and `/ready` are open. Errors are RFC 9457 `application/problem+json`.
`tests/test_oidc_entry_points.py` proves, for every route of every service, that HS256, wrong-audience, ID, expired, foreign-issuer and
missing tokens are refused. The `identity` service no longer issues tokens: it keeps a profile mirror (`GET /users/me`).
The services keep their internal, unversioned paths; the public Agent API is the monolith's `/api/v1` (`contracts/agents/v1`).
