---
applyTo: "tests/**"
description: Testing conventions for the agent framework
---

# Testing Conventions

## Framework
- **pytest + pytest-asyncio** — all tests in `tests/`
- Run: `uv run pytest`
- Format before committing: `uv run ruff format . && uv run ruff check .`

## Async tests
```python
import pytest

@pytest.mark.asyncio
async def test_something():
    ...
```

## Tool unit tests
Test tools directly without spinning up the full server:
```python
from agent_framework.tools.my_tool import MyTool

@pytest.mark.asyncio
async def test_my_tool():
    tool = MyTool()
    result = await tool.execute(param="value")
    assert result.content == "expected"
```

## Agent integration tests
Use `MemoryClient` (or mock `OpenAIClient`) to avoid real API calls:
```python
from unittest.mock import AsyncMock, patch

@pytest.mark.asyncio
async def test_agent_run():
    with patch("agent_framework.model_clients.openai.openai_client.OpenAIClient") as mock:
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
