PYTHON_VERSION ?= 3.13
TEST_DATABASE_URL ?= postgresql+asyncpg://postgres:postgres@localhost:5432/agentdb
TEST_REDIS_URL ?= redis://localhost:6379/0
TEST_OPENAI_API_KEY ?= sk-test-placeholder
TEST_JWT_SECRET ?= test-jwt-secret-not-for-production-use-0000000000

ifeq ($(OS),Windows_NT)
RUN_TEST_CI = cmd /C "set VIRTUAL_ENV=&&set DATABASE_URL=$(TEST_DATABASE_URL)&&set REDIS_URL=$(TEST_REDIS_URL)&&set OPENAI_API_KEY=$(TEST_OPENAI_API_KEY)&&set JWT_SECRET=$(TEST_JWT_SECRET)&&uv run python -m pytest --tb=short -q --junitxml=test-results.xml"
else
RUN_TEST_CI = DATABASE_URL=$(TEST_DATABASE_URL) REDIS_URL=$(TEST_REDIS_URL) OPENAI_API_KEY=$(TEST_OPENAI_API_KEY) JWT_SECRET=$(TEST_JWT_SECRET) uv run pytest --tb=short -q --junitxml=test-results.xml
endif

.PHONY: sync lint lint-apply lint-imports protocol-schema format-check typecheck typecheck-soft test test-ci build security security-soft ci help start start-reload infra-up infra-up-docling infra-up-sandbox infra-down docker-up docker-down observability-up observability-down

help:
	@echo "Available targets:"
	@echo "  make sync         - install project dependencies"
	@echo "  make start        - start the backend in foreground via uv run start"
	@echo "  make start-reload - start the backend with auto-reload (requires make infra-up)"
	@echo "  make infra-up     - start host-dev support services (Postgres, Redis, MinIO, Loki, Promtail, Grafana, Tempo, MCP server)"
	@echo "  make infra-up-docling - build and start the Docling extraction service (opt-in, ~4GB image)"
	@echo "  make infra-up-sandbox - build and start the local code-interpreter sandbox (opt-in)"
	@echo "  make infra-down   - stop the host-dev support services"
	@echo "  make docker-up    - build and start the Docker backend plus core infra and storage"
	@echo "  make docker-down  - stop the full agent-framework Docker stack"
	@echo "  make observability-up   - start Tempo and Grafana via Docker Compose"
	@echo "  make observability-down - stop Tempo and Grafana"
	@echo "  make lint         - run Ruff lint and format checks"
	@echo "  make lint-imports - run import-linter (kernel independence + layer contracts)"
	@echo "  make audit-dead-symbols - scan for module-level functions/classes with no real usage (manual, not in ci)"
	@echo "  make typecheck    - run Pyright (hard fail)"
	@echo "  make test         - run pytest"
	@echo "  make build        - build the backend Docker image manually"
	@echo "  make security     - run pip-audit (hard fail; see Makefile for the documented ignore-list)"
	@echo "  make ci           - run the same preflight used by CI (lint, lint-imports, typecheck, test, security — all blocking)"

start:
	uv run start

start-reload:
	uv run start --reload

infra-up:
	docker compose --env-file .env -f ./deployment/docker/docker-compose.yml --profile runtime up -d --remove-orphans postgres redis minio loki promtail tempo grafana mcp-server

infra-up-docling:
	docker compose --env-file .env -f ./deployment/docker/docker-compose.yml --profile docling up -d --build docling

infra-up-sandbox:
	docker compose --env-file .env -f ./deployment/docker/docker-compose.yml --profile sandbox up -d --build code-interpreter-sandbox

infra-down:
	docker compose --env-file .env -f ./deployment/docker/docker-compose.yml --profile runtime stop postgres redis minio loki promtail tempo grafana mcp-server

docker-up:
	docker compose --env-file .env -f ./deployment/docker/docker-compose.yml --profile runtime up -d --build --remove-orphans backend postgres redis minio loki promtail tempo grafana mcp-server

docker-down:
	docker compose --env-file .env -f ./deployment/docker/docker-compose.yml down --remove-orphans

observability-up:
	docker compose --env-file .env -f ./deployment/docker/docker-compose.yml up -d tempo grafana

observability-down:
	docker compose --env-file .env -f ./deployment/docker/docker-compose.yml stop tempo grafana

sync:
	uv python install $(PYTHON_VERSION)
	uv sync --extra server

lint-apply:
	uv run ruff check . --fix

protocol-schema:
	uv run python -m substrate.serving.protocol.export

lint:
	uv run ruff check .
	uv run ruff format --check .

format-check:
	uv run ruff format --check .

lint-imports:
	uv run lint-imports

audit-dead-symbols:
	uv run python scripts/find_dead_symbols.py

typecheck:
	uv run --with pyright pyright src/

typecheck-soft:
	@$(MAKE) typecheck || echo "Non-blocking: typecheck failures ignored by ci target"

test:
	uv run pytest --tb=short -q

test-ci:
	$(RUN_TEST_CI)

build:
	docker build -f ./deployment/docker/backend.Dockerfile .

# Ignored CVEs below are all confirmed blocked by a hard version pin in an
# upstream dependency we can't override without dropping the feature that
# needs it (verified via `uv lock` with a forcing constraint — each fails
# with an explicit "X depends on Y<Z" resolution error). Re-check by removing
# an entry and running `make security` after bumping the blocking package.
# (paddleocr[all] was removed entirely — it was an unused dependency and the
# sole reason the langchain/langchain-openai/langchain-text-splitters CVEs
# were in the tree at all.)
#   PYSEC-2026-282 (apscheduler, RCE via unmarshal_object) — no fix version
#     exists upstream yet. Not reachable today: we only ever construct
#     AsyncScheduler(data_store=MemoryDataStore()) (capabilities/triggers/
#     scheduler.py) — no persistent data store, so the vulnerable
#     serialize/deserialize round-trip never runs. Re-audit if the data
#     store is ever changed to a persistent backend.
#   PYSEC-2026-87 (lxml, XXE-style local file read via default entity
#     resolution) — pulled in transitively via crawl4ai, which pins
#     lxml<6.dev0 (the fixed version is 6.1.0). We never import lxml
#     directly or construct our own parser with custom entity-resolution
#     settings.
#   PYSEC-2026-597 (nltk, path traversal in url2pathname) — no fix version
#     exists upstream yet. Pulled in transitively via crawl4ai only; we
#     never import nltk directly or call its data-download helpers.
SECURITY_IGNORES = \
	--ignore-vuln PYSEC-2026-282 \
	--ignore-vuln PYSEC-2026-87 \
	--ignore-vuln PYSEC-2026-597

security:
	uv run --with pip-audit pip-audit $(SECURITY_IGNORES)

security-soft:
	@$(MAKE) security || echo "Non-blocking: security findings ignored by ci target"

ci:
	$(MAKE) sync
	$(MAKE) lint-apply
	$(MAKE) lint
	$(MAKE) lint-imports
	$(MAKE) typecheck
	$(MAKE) test-ci
	$(MAKE) security