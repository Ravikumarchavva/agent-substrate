.PHONY: setup up down dev dev-compute demo bench bench-engines test test-unit test-postgres test-vending test-processes check compute-image

setup:
	uv sync --locked
	cd web && npm ci --legacy-peer-deps

# Storage + Iceberg REST catalog (SeaweedFS 4.48) and the identity provider (Keycloak).
up:
	uv run scripts/bootstrap.py
	docker compose --env-file .env -f deployment/infra.compose.yml up -d
	uv run scripts/infra_ready.py

down:
	docker compose --env-file .env -f deployment/infra.compose.yml down

# Control plane: controller (8014), API (8020), UI (3011). Starts no compute.
dev:
	./scripts/dev.sh

# Stand-alone workers operated outside the application, attached to the controller.
dev-compute:
	./scripts/dev-compute.sh

# Load NYC yellow taxi months into main.nyc.yellow (needs `make dev` and `make dev-compute`).
demo:
	uv run scripts/demo.py

# Latency by query class against main.nyc.yellow (needs `make demo`).
bench:
	uv run benchmarks/latency.py

# DuckDB vs Polars on the same files; add `-- --local` to read from local disk instead of S3.
bench-engines:
	uv run benchmarks/engines.py

test:
	uv run pytest -q
	cd web && npm test

test-unit:
	SUBSTRATE_DATA_SKIP_INTEGRATION=1 uv run pytest -q

test-postgres:
	SUBSTRATE_DATA_TEST_CATALOG=postgres uv run pytest tests

test-vending:
	SUBSTRATE_DATA_TEST_VENDING=1 uv run pytest tests

test-processes:
	SUBSTRATE_DATA_TEST_PROCESSES=1 uv run pytest -q tests/test_compute_processes.py tests/test_compute_control_processes.py

check:
	uv run ruff check src tests scripts benchmarks
	uv run ruff format --check src tests scripts benchmarks
	cd web && npm run typecheck && npm run lint && npm run build

# Worker image for operators to run wherever they host compute; the application never launches it.
compute-image:
	docker build -f deployment/compute.Dockerfile -t substrate-data-worker:local .
