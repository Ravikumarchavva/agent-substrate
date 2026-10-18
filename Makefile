# Root entry points for Loamz monorepo.
.PHONY: sync lint format test test-fast test-agents test-data imports check js-check clean-venv help

PY_PACKAGES := agents/library data/storage data/client data/server identity/python meta/loamz

help:
	@echo "make sync          install every Python package (uv) and the JS workspace (pnpm)"
	@echo "make lint          ruff check + format --check over all Python packages"
	@echo "make imports       import-linter architecture contracts"
	@echo "make test-agents   loamz-agents tests"
	@echo "make test-data     loamz-data-storage, loamz-data-client, loamz-identity tests"
	@echo "make test          run all fast unit tests"
	@echo "make check         lint + imports + fast tests"

sync:
	uv sync --all-packages
	pnpm install

lint:
	uv run ruff check $(PY_PACKAGES)
	uv run ruff format --check $(PY_PACKAGES)

format:
	uv run ruff format $(PY_PACKAGES)

imports:
	uv run --package loamz-agents lint-imports --config .importlinter

test-data:
	uv run --package loamz-identity pytest identity/python/tests -q
	uv run --package loamz-data-client pytest data/client/tests -q
	uv run --package loamz-data-storage pytest data/storage/tests/test_sql.py data/storage/tests/test_okf.py -q

test-agents:
	uv run --package loamz-agents pytest agents/library/tests/features/ agents/library/tests/data/ -q

js-check:
	pnpm -r typecheck
	pnpm -r test

test: test-data test-agents

check: lint imports test
