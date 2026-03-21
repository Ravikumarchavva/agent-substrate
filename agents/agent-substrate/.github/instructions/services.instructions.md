---
applyTo: "src/agent_framework/services/**"
---

## Microservice Structure Conventions

Every complete service follows this standard layout:

```
services/<name>/
├── app.py       ← FastAPI app factory + lifespan; wires app.state.*
├── models.py    ← SQLAlchemy ORM models (service-private DB tables)
├── routes.py    ← APIRouter with all HTTP endpoints
├── service.py   ← Business logic layer
└── __init__.py
```

Services intentionally omitting `models.py`/`service.py` by design:
- `gateway` — BFF proxy, no business logic or own tables
- `live_stream` — SSE projector, uses `projector.py` instead
- `tool_executor` — executor pattern, uses `executor.py` instead

## FastAPI App Pattern

```python
# app.py
from __future__ import annotations
from contextlib import asynccontextmanager
from fastapi import FastAPI
from agent_framework.configs.settings import get_settings
from agent_framework.shared.events.bus import EventBus
from .routes import router

settings = get_settings()

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.bus = EventBus(redis_url=settings.REDIS_URL)
    await app.state.bus.connect()
    yield
    await app.state.bus.disconnect()

app = FastAPI(title="<service-name>", lifespan=lifespan)
app.include_router(router)
```

## Shared Event Bus

Always emit events via `EventBus` after committed DB writes:

```python
from agent_framework.shared.events.bus import EventBus
from agent_framework.shared.events.types import workflow_started, workflow_failed

bus: EventBus = request.app.state.bus
await bus.publish(workflow_started(job_id=job.id, run_id=run.id))
```

Use factory functions from `shared/events/types.py` — **never** build event dicts manually.

## Shared Contracts

All cross-service DTOs live in `shared/contracts/<service_name>.py`:

```python
from agent_framework.shared.contracts.job_controller import JobRunRequest
from agent_framework.shared.contracts.human_gate import HITLResponse
from agent_framework.shared.contracts.file_store import FileUploadResponse
```

## Database Access

Use SQLAlchemy async sessions. The session is typically wired via `app.state`:

```python
async with app.state.db() as session:
    result = await session.execute(select(MyModel).where(MyModel.id == id))
    row = result.scalar_one_or_none()
```

## Service Layer Rules

- Service methods are `async def` and accept a `session` parameter for DB access.
- Emit `EventBus` events **after** successful DB commits, not before.
- Raise `HTTPException` from **routes**, not from service methods.
- Keep route handlers thin — all logic belongs in `service.py`.
- ORM model classes live in `models.py`; import them only within the same service.

## Naming Conventions in Job Controller

The job controller ORM model is `JobRun` (not `WorkflowRun`).
Event factories for it are: `workflow_started`, `workflow_completed`, `workflow_failed`, `workflow_cancelled`
(names kept from prior naming; the model rename was internal only).
