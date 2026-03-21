## What changed

<!-- Describe the changes made in this PR -->

## Why

<!-- Motivation and context. Link any related issues: Fixes #123 -->

## How to test

```bash
uv run pytest
uv run ruff check .
```

<!-- Add any manual test steps if relevant -->

## Checklist

- [ ] Tests pass (`uv run pytest`)
- [ ] Lint is clean (`uv run ruff check .`)
- [ ] No hardcoded secrets or API keys
- [ ] All new handlers/service methods are `async def`
- [ ] All memory calls are `await`ed
- [ ] Type annotations added for all new functions
