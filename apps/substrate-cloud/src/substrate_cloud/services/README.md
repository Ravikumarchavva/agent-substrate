# Microservices

11 independent FastAPI applications, one per folder.

Each service follows a standard layout (`app.py`, `routes.py`, `service.py`, `models.py`)
except `gateway` (BFF proxy), `live_stream` (SSE projector), and `tool_executor` (executor pattern)
which intentionally omit `models.py`/`service.py`.

Service-to-service communication uses Pydantic DTOs from `shared/contracts/`
and factory-built events from `shared/events/types.py`.

See `CLAUDE.md` → "Microservices — Roles & ORM Models" for the full table.
