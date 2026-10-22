# Root entry points. Python: one uv workspace (uv.lock); JavaScript: one pnpm workspace.
# The fast tier (`make test`) needs no infrastructure and finishes in a few minutes. PostgreSQL and Redis tests run when
# LOAMZ_TEST_POSTGRES_URL / LOAMZ_TEST_REDIS_URL point at a server, and skip otherwise.
.PHONY: sync lint format imports test test-sdk test-engine test-server test-reader test-slow js-check check e2e help

PY_DIRS := server/engine server/server server/reader sdk/python/core sdk/python/data meta/loamz
PYTEST := -q -n 4 --timeout=120

help:
	@echo "make sync         install every Python package (uv) and the JS workspace (pnpm)"
	@echo "make lint         ruff check + format --check"
	@echo "make imports      import-linter architecture contracts (.importlinter)"
	@echo "make test         the fast tier: sdk, engine, server, reader"
	@echo "make test-slow    the Docker/model-backed tests"
	@echo "make e2e          the cell end-to-end test (needs the compose cell up: deploy/compose)"
	@echo "make check        lint + imports + test"

sync:
	uv sync --all-packages --all-extras --all-groups
	pnpm install

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff format .

imports:
	uv run lint-imports --config .importlinter

test-sdk:
	cd sdk/python/core && uv run --package loamz-core pytest $(PYTEST)
	cd sdk/python/data && uv run --package loamz-data pytest $(PYTEST)

test-engine:
	cd server/engine && uv run --package loamz-engine pytest $(PYTEST)

test-server:
	cd server/server && uv run --package loamz-server pytest $(PYTEST)

# loamz-reader runs on Python 3.13 with its own lock, outside the workspace.
test-reader:
	cd server/reader && uv run --python 3.13 --frozen --extra test python -m pytest -q

test: test-sdk test-engine test-server test-reader

test-slow:
	cd server/server && uv run --package loamz-server pytest -q -m "slow or integration" --timeout=300
	cd server/engine && uv run --package loamz-engine pytest -q -m "slow" --timeout=300

js-check:
	pnpm -r typecheck
	pnpm -r test

e2e:
	uv run --package loamz-core python deploy/compose/e2e.py deploy/compose/.env

check: lint imports test js-check
