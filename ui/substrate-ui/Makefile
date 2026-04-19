.PHONY: install lint typecheck build smoke security security-soft ci dev help

help:
	@echo "Available targets:"
	@echo "  make install      - install project dependencies and regenerate Prisma client"
	@echo "  make lint         - run ESLint"
	@echo "  make typecheck    - run TypeScript type checking"
	@echo "  make build        - production build"
	@echo "  make smoke        - route smoke test (needs build first)"
	@echo "  make security     - run pnpm audit"
	@echo "  make ci           - run the full local preflight (install → lint → typecheck → build → smoke → security-soft)"
	@echo "  make dev          - clear .next cache and start dev server (prevents post-build 404s)"

install:
	pnpm install --frozen-lockfile --ignore-scripts
	pnpm prisma:generate

lint:
	pnpm lint

typecheck:
	pnpm tsc --noEmit

build:
	pnpm build

smoke:
	node scripts/smoke-routes.mjs

security:
	pnpm audit --audit-level=high

security-soft:
	@$(MAKE) security || echo "Non-blocking: audit findings ignored by ci target"

dev:
	@if exist .next (rmdir /s /q .next) else (echo "No .next cache to clear")
	pnpm dev

ci:
	$(MAKE) install
	$(MAKE) lint
	$(MAKE) typecheck
	$(MAKE) build
	$(MAKE) smoke
	$(MAKE) security-soft
