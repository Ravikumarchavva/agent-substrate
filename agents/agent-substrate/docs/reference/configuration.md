# Configuration

These are the environment variables you will touch most often.

## Core runtime

```env
OPENAI_API_KEY=
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/agentdb
REDIS_URL=redis://localhost:6379/0
REDIS_SESSION_TTL=3600
SESSION_MAX_MESSAGES=200
SESSION_AUTO_CHECKPOINT=50
```

## Durable runtime

```env
RESTATE_INGRESS_URL=http://localhost:8080
RESTATE_ADMIN_URL=http://localhost:9070
```

## Frontend

```env
NEXT_PUBLIC_API_URL=http://localhost:8000
BACKEND_API_URL=http://localhost:8000
```

## Important rule

Do not add inline comments after integer values in `.env` files. They can break parsing and validation.