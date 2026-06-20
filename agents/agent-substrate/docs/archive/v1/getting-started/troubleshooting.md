# Troubleshooting

## Common startup issues

### `OPENAI_API_KEY` not set

Set it in `.env` or export it in your shell before running the backend.

### PostgreSQL or Redis connection errors

Start the local infrastructure:

```bash
docker compose -f deployment/docker/docker-compose.yml up -d postgres redis
```

### Port already in use

- backend: `8000`
- PostgreSQL: `5432`
- Redis: `6379`
- Grafana: `3001`

### Import errors after pulling changes

Run:

```bash
uv sync
```

### Docs or runtime drift

Use the repo-quality checks:

```bash
make ci
```

## Debug checklist

1. Verify `.env` values.
2. Confirm Redis and PostgreSQL are healthy.
3. Check `uvicorn` startup logs for dependency wiring failures.
4. Run `uv run pytest` if you suspect a local code regression.