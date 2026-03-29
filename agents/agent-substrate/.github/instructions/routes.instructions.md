---
name: "Server Route Conventions"
description: "FastAPI server patterns, dependency injection, SSE streaming, and route registration"
applyTo: "src/raavan/server/**"
---

# Server & Route Conventions

## Router file layout
Each feature gets its own file under `server/routes/`:
```
server/routes/
  chat.py         POST /chat            SSE streaming endpoint
  hitl.py         GET  /hitl/events     SSE HITL bridge
  threads.py      CRUD /threads
  tasks.py        CRUD /tasks/{conversation_id}
  mcp_apps.py          /apps            MCP App registry
  spotify_oauth.py     /auth/spotify
  elements.py          /elements
  code_interpreter.py  /code
```

## Dependency injection
Use `app.state.*` — never import global singletons inside routes:
```python
bridge: WebHITLBridge  = request.app.state.bridge
tools: list            = request.app.state.tools
model_client           = request.app.state.model_client
session_factory        = request.app.state.session_factory
```

## Database sessions
Use `Depends(get_db)` for handlers that need DB access:
```python
@router.get("/endpoint")
async def handler(db: AsyncSession = Depends(get_db)):
    ...
    await db.commit()
```

## SSE streaming pattern
```python
async def sse_generator():
    # Merge agent chunks + bridge events via asyncio.Queue
    merged_queue = asyncio.Queue()
    agent_task = asyncio.create_task(agent_worker())
    hitl_task  = asyncio.create_task(hitl_worker())
    while True:
        source, data = await merged_queue.get()
        if source == "done": break
        yield f"data: {json.dumps(payload)}\n\n"
return StreamingResponse(sse_generator(), media_type="text/event-stream")
```

## Adding a new route
1. Create `server/routes/my_feature.py` with `router = APIRouter(prefix="/my-feature")`
2. Import and mount in `server/app.py → create_app()`:
   `app.include_router(my_feature_router)`
3. Add corresponding Pydantic schemas to `server/schemas.py`.

## Lifespan ordering (server/app.py)
1. OpenTelemetry setup
2. Database init
3. Create `OpenAIClient`
4. Create `WebHITLBridge`
5. Instantiate tools (tools needing bridge get `event_emitter=bridge.put_event`)
6. Set `app.state.tools`, `app.state.system_instructions`, `app.state.bridge`
7. `yield`  ← server is live from here
8. Cleanup: close CI client, stop tools with `.stop()`, close DB, shutdown OTel.
