PYTHON_VERSION ?= 3.13
TEST_DATABASE_URL ?= postgresql+asyncpg://postgres:postgres@localhost:5432/agentdb
TEST_REDIS_URL ?= redis://localhost:6379/0
TEST_OPENAI_API_KEY ?= sk-test-placeholder

ifeq ($(OS),Windows_NT)
RUN_TEST_CI = cmd /C "set VIRTUAL_ENV=&&set DATABASE_URL=$(TEST_DATABASE_URL)&&set REDIS_URL=$(TEST_REDIS_URL)&&set OPENAI_API_KEY=$(TEST_OPENAI_API_KEY)&&uv run python -m pytest --tb=short -q --junitxml=test-results.xml"
else
RUN_TEST_CI = DATABASE_URL=$(TEST_DATABASE_URL) REDIS_URL=$(TEST_REDIS_URL) OPENAI_API_KEY=$(TEST_OPENAI_API_KEY) uv run pytest --tb=short -q --junitxml=test-results.xml
endif

.PHONY: sync lint format-check typecheck typecheck-soft test test-ci build security security-soft ci help

help:
	@echo "Available targets:"
	@echo "  make sync         - install project dependencies"
	@echo "  make lint         - run Ruff lint and format checks"
	@echo "  make typecheck    - run Pyright (soft fail remains in CI workflow)"
	@echo "  make test         - run pytest"
	@echo "  make build        - build the backend Docker image manually"
	@echo "  make security     - run pip-audit"
	@echo "  make ci           - run the same local preflight used by CI (typecheck/security are non-blocking)"

sync:
	uv python install $(PYTHON_VERSION)
	uv sync

lint:
	uv run ruff check .
	uv run ruff format --check .

format-check:
	uv run ruff format --check .

typecheck:
	uv run --group browser --group files --group storage --with pyright pyright src/

typecheck-soft:
	@$(MAKE) typecheck || echo "Non-blocking: typecheck failures ignored by ci target"

test:
	uv run pytest --tb=short -q

test-ci:
	$(RUN_TEST_CI)

build:
	docker build -f ./deployment/docker/backend.Dockerfile .

security:
	uv run --with pip-audit pip-audit

security-soft:
	@$(MAKE) security || echo "Non-blocking: security findings ignored by ci target"

ci:
	$(MAKE) sync
	$(MAKE) lint
	$(MAKE) typecheck-soft
	$(MAKE) test-ci
	$(MAKE) security-soft