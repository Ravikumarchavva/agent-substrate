# Local And Kind

## Local monolith

This is the best development path for backend iteration.

```bash
docker compose -f deployment/docker/docker-compose.yml up -d postgres redis
uv run uvicorn raavan.server.app:app --port 8000 --reload
```

## Why use it

- shortest feedback loop
- easiest debugging path
- no service-to-service networking overhead

## Kind deployment

Use Kind when you need ingress, namespaces, deployment behavior, or the observability stack.

```bash
uv run python deploy.py
```

This deploys the stack into a local Kubernetes cluster using the repo’s manifests and overlays.