.PHONY: install lint typecheck build security security-soft ci help

help:
	@echo "Available targets:"
	@echo "  make install      - install project dependencies"
	@echo "  make lint         - run ESLint"
	@echo "  make typecheck    - run TypeScript type checking"
	@echo "  make build        - production build"
	@echo "  make security     - run pnpm audit"
	@echo "  make ci           - run the full local preflight (install → lint → typecheck → build → security-soft)"

install:
	pnpm install

lint:
	pnpm lint

typecheck:
	pnpm tsc --noEmit

build:
	pnpm build

security:
	pnpm audit --audit-level=high

security-soft:
	@$(MAKE) security || echo "Non-blocking: audit findings ignored by ci target"

ci:
	$(MAKE) install
	$(MAKE) lint
	$(MAKE) typecheck
	$(MAKE) build
	$(MAKE) security-soft
