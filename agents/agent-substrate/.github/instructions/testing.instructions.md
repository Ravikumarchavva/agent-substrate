---
name: "Testing Conventions"
description: "pytest-asyncio patterns, tool unit tests, agent integration tests, and DB session fixtures"
applyTo: "tests/**"
---

# Testing Conventions

## Framework
- **pytest + pytest-asyncio** — all tests in `tests/`
- Run: `uv run pytest`
- Format before committing: `uv run ruff format . && uv run ruff check .`

## Async tests
`pyproject.toml` sets `asyncio_mode = "auto"` — **no `@pytest.mark.asyncio` decorator needed**.
Just write `async def test_*` and pytest-asyncio picks it up automatically:
```python
async def test_something():
    result = await my_async_func()
    assert result == expected
```

## Tool unit tests
Test tools directly without spinning up the full server:
```python
from raavan.core.tools.base_tool import BaseTool, ToolResult

async def test_my_tool():
    tool = MyTool()
    result = await tool.execute(param="value")
    assert result.content == "expected"
```

## Agent integration tests
Use mock `OpenAIClient` to avoid real API calls:
```python
from unittest.mock import AsyncMock, patch

async def test_agent_run():
    with patch("raavan.integrations.llm.openai.openai_client.OpenAIClient") as mock:
        mock.return_value.stream = AsyncMock(...)
        agent = ReActAgent(model_client=mock.return_value, tools=[])
        async for chunk in agent.run_stream("hello"):
            ...
```

## Database tests
Use a test database URL with `asyncpg`. Wrap each test in a transaction that rolls back:
```python
@pytest.fixture
async def db_session():
    async with get_session_factory()() as session:
        yield session
        await session.rollback()
```

## SSE/streaming tests
Collect SSE output into a list:
```python
events = []
async for chunk in agent.run_stream("query"):
    events.append(chunk)
assert any(isinstance(e, TextDeltaChunk) for e in events)
```

## Shared test fixtures
`tests/conftest.py` provides reusable fixtures:
- `mock_llm_client` — `AsyncMock` of `OpenAIClient` (yields empty stream)
- `redis_memory` — `RedisMemory` (skipped when Redis unavailable)
- `tool_registry` — empty `ToolRegistry`
- `tmp_file_store` — `LocalFileStore` backed by `tmp_path`
