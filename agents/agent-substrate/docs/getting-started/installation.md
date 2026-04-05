# Installation

## Prerequisites

- Python 3.13
- `uv`
- Docker for Redis and PostgreSQL
- an LLM provider key such as `OPENAI_API_KEY`

## Install the project

```bash
git clone https://github.com/Ravikumarchavva/raavan.git
cd raavan
uv sync
```

## Optional dependency groups

```bash
uv sync --group notebooks
uv sync --group browser --group files
uv sync --group storage
```

## Minimal environment

Create `.env` at the repo root:

```env
OPENAI_API_KEY=sk-...
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/agentdb
REDIS_URL=redis://localhost:6379/0
```

## Next step

Continue to [Quickstart](quickstart.md).