# Monolith Server

Single FastAPI application — the default deployment mode.

- **Entry point**: `app.py` → `create_app()` factory + lifespan wiring via `app.state.*`
- **Routes**: one file per feature in `routes/` (chat, tasks, hitl, threads, mcp_apps, …)
- **Schemas**: `schemas.py` — Pydantic models for this server only (microservices use `shared/contracts/`)
- **DB models**: `models.py` — SQLAlchemy ORM models
- **Run**: `uv run uvicorn raavan.server.app:app --port 8000 --reload`
