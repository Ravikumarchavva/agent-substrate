# Root entry points. Each package keeps its own Makefile for its own concerns; these run the common checks
# across the workspace. Python: uv workspace (uv.lock). JavaScript: pnpm workspace (pnpm-lock.yaml).
.PHONY: sync lint format test test-slow test-agents test-data test-services imports smoke check js-install js-check clean-venv help

PY_PACKAGES := agents/agent-substrate agents/substrate-cloud data/substrate-data data/substrate-data-client data/embedding-reranker data/inference-pool

help:
	@echo "make sync          install every Python package (uv) and the JS workspace (pnpm)"
	@echo "make lint          ruff check + format --check over all Python packages"
	@echo "make imports       import-linter contracts of the agent library and the platform"
	@echo "make test-agents   agent-substrate + substrate-cloud (substrate-cloud needs Postgres 'agentdb')"
	@echo "make test-data     substrate-data unit tests + client (integration: make -C data/substrate-data test)"
	@echo "make test-services embedding-reranker + inference-pool"
	@echo "make smoke         the Agent API in both modes, in process (no Docker)"
	@echo "make check         lint + imports + the tests that need no infrastructure"

sync:
	uv sync --all-packages --frozen
	pnpm install --frozen-lockfile

lint:
	uv run ruff check $(PY_PACKAGES)
	uv run ruff format --check $(PY_PACKAGES)

format:
	uv run ruff format $(PY_PACKAGES)

# Both deployment modes of the agent server, in process, no Docker (the monolith part needs the local PostgreSQL 'agentdb').
smoke:
	cd agents/substrate-cloud && uv run --package substrate-cloud python scripts/smoke.py both

imports:
	cd agents/agent-substrate && uv run --package agent-substrate lint-imports
	cd agents/substrate-cloud && uv run --package substrate-cloud lint-imports

# `make test` is the fast tier (a few minutes, no infrastructure): lint, import contracts and the default test selection of
# every package. Postgres-backed and model-loading tests are marked `slow`/`heavy`/`integration` and run in CI or `make test-slow`.
test-agents:
	cd agents/agent-substrate && uv run --package agent-substrate pytest tests -q -n 6 --timeout=120
	cd agents/substrate-cloud && OTEL_SDK_DISABLED=true uv run --package substrate-cloud pytest -q --timeout=60

test-data:
	cd data/substrate-data && SUBSTRATE_DATA_SKIP_INTEGRATION=1 uv run --package substrate-data pytest tests -q -n 4 --timeout=120 -m "not integration"

test-slow:
	cd agents/substrate-cloud && OTEL_SDK_DISABLED=true uv run --package substrate-cloud pytest -q -m "slow" --timeout=120

test-services:
	cd data/inference-pool && uv run --package inference-pool pytest -q
	cd data/embedding-reranker && uv run --package embedding-reranker pytest -q

js-check:
	pnpm -r typecheck
	pnpm -r test

test: test-agents test-data test-services

check: lint imports test
