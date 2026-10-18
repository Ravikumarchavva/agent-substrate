# Contributing

Thanks for helping. No DCO or CLA is required.

## Flow

Trunk-based: `main` is always releasable. Branch from `main`, keep branches short-lived, open a PR, and squash-merge.
CI must be green: the single required check is `ci-ok`.

- **PR titles are conventional commits** (checked by CI) because the squash commit message drives releases:
  `type(scope): subject`, for example `fix(substrate-data): reject expired vending tokens`.
  Types: `feat fix perf refactor docs test build ci chore revert`. Scopes are package names plus `contracts`,
  `identity`, `deploy`, `docs`, `adr`, `ci`, `deps`, `repo`. Use `feat!:` or a `BREAKING CHANGE:` footer for breaking changes.
- Releases are automated by release-please, per package, from those commits. Do not edit versions or changelogs by hand.
- Architectural decisions get an ADR: see [docs/adr/README.md](docs/adr/README.md).
- Contract changes (`contracts/`, public API schemas) must be checked with `oasdiff`; APIs are versioned under `/api/v1`.
- Never commit secrets (`.env*`, keys, tokens).

## Running checks locally

Python uses one uv workspace at the root (Python 3.14). Use `uv`, never `pip`.

```bash
uv sync --all-packages

# agent-substrate (the library)
cd agents/agent-substrate
uv run ruff check . && uv run ruff format --check .
uv run lint-imports                  # layer/import boundaries
uv run --with pyright pyright src/   # non-blocking in CI
uv run pytest -q

# substrate-cloud
cd agents/substrate-cloud
uv run ruff check . && uv run ruff format --check . && uv run lint-imports && uv run pytest -q

# substrate-data (integration tests need Docker; skip them for a quick run)
cd data/substrate-data
SUBSTRATE_DATA_SKIP_INTEGRATION=1 uv run pytest -q
uv run pytest -q tests              # full, with Docker

# substrate-data-client, embedding-reranker, inference-pool
cd data/<package> && uv run ruff check . && uv run ruff format --check . && uv run pytest -q
```

`data/document-intelligence` is outside the workspace (Python 3.13, own project):

```bash
cd data/document-intelligence && uv sync && uv run ruff check . && uv run pytest -q
```

JavaScript uses one pnpm workspace at the root (Node 22):

```bash
pnpm install --frozen-lockfile
pnpm --filter ./agents/substrate-ui lint
pnpm --filter ./agents/substrate-ui typecheck
pnpm --filter ./agents/substrate-ui test
pnpm --filter ./agents/substrate-ui build
# same four scripts for ./data/substrate-data-web
```

## CI behavior

CI detects which packages changed and also runs their dependents (a change to `agents/agent-substrate` runs most
Python packages; `contracts/`, `identity/`, the root `pyproject.toml` or `uv.lock` run everything). The slow
`substrate-data` integration tests run on merge-queue runs, or on a PR labelled `run-integration`.
