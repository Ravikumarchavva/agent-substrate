# First Durable Run

After the quickstart, the next step is to run the real backend path with infrastructure and streaming.

## Start infrastructure

```bash
docker compose -f deployment/docker/docker-compose.yml up -d postgres redis
```

## Start the backend

```bash
uv run uvicorn raavan.server.app:app --port 8000 --reload
```

## What this starts

- FastAPI server
- database session wiring
- Redis-backed runtime services
- model client wiring in `app.state.*`
- SSE endpoints for streaming chat updates

## Try a request

Use the frontend or call the backend directly.

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Explain the ReAct loop in 3 bullet points"}'
```

## What to look for

- streamed text or tool events
- database writes for threads and messages
- logs from the FastAPI app and runtime path

## Next step

Read [Streaming And Events](../concepts/streaming-and-events.md) and [Local And Kind](../deploy/local-and-kind.md).