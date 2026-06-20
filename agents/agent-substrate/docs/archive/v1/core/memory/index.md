# History & Memory Providers

Memory in Ravi is managed by implementations of the `HistoryProvider` protocol. They store and retrieve chronological conversation transcripts, tagged by `agent_id`, `session_id`, and `run_id`.

There are three concrete implementations of `HistoryProvider` available depending on your durability and storage needs.

---

## The three tiers

```mermaid
graph TB
    subgraph "In-process (no infra)"
        UM["InMemoryHistoryProvider\nPython list in RAM\nLost on restart"]
    end
    subgraph "Hot tier (Redis)"
        RM["RedisHistoryProvider\nFast reads / writes\nShared cache\nSurvives process restarts"]
    end
    subgraph "Cold tier (Postgres)"
        PM["PostgresHistoryProvider\nDurable SQL database\nFull conversation audit trail"]
    end

    UM -.-->|"upgrade for persistence"| RM
    RM -.-->|"upgrade for relational DB"| PM
```

---

## HistoryProvider Protocol

All memory providers implement the same `HistoryProvider` interface:

```python
class HistoryProvider(Protocol):
    async def append(
        self,
        agent_id: AgentId,
        message: ChatMessage,
        *,
        session_id: str,
        run_id: str = "",
    ) -> None: ...

    async def append_many(
        self,
        agent_id: AgentId,
        messages: list[ChatMessage],
        *,
        session_id: str,
        run_id: str = "",
    ) -> None: ...

    async def get_messages(
        self,
        agent_id: AgentId,
        *,
        session_id: str,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[ChatMessage]: ...

    async def clear(self, agent_id: AgentId, *, session_id: str) -> None: ...

    async def clear_run(
        self, agent_id: AgentId, *, session_id: str, run_id: str
    ) -> None: ...

    async def count_messages(self, agent_id: AgentId, *, session_id: str) -> int: ...
```

---

## InMemoryHistoryProvider — in-process

The simplest choice: an in-memory list stored in RAM. Use it for scripts, notebooks, and unit tests where crash recovery is not needed.

```python
from ravi.agents.context import InMemoryHistoryProvider
from ravi.kernel import ChatMessage, Role, TextBlock
from ravi.kernel.core.identity import AgentId

mem = InMemoryHistoryProvider()
agent_id = AgentId(type="assistant", key="helper")

# Add message
await mem.append(
    agent_id=agent_id,
    message=ChatMessage(role=Role.USER, content=[TextBlock(text="Hello")]),
    session_id="session-123",
    run_id="run-456",
)

# Retrieve messages
msgs = await mem.get_messages(agent_id, session_id="session-123")
```

---

## RedisHistoryProvider — hot tier

Conversation history lives in Redis. This allows multiple workers to share the same history pool, and history survives agent worker process restarts.

```python
from ravi.capabilities.history.redis_history import RedisHistoryProvider
from ravi.kernel.core.identity import AgentId

mem = RedisHistoryProvider(
    redis_url="redis://localhost:6379/0",
)

agent_id = AgentId(type="assistant", key="helper")

# Appends and reads are executed directly against Redis keys
await mem.append(
    agent_id=agent_id,
    message=msg,
    session_id="session-123",
)
```

---

## PostgresHistoryProvider — cold tier

A fully durable relational database history provider using SQLAlchemy/asyncpg. It compiles conversation steps and audits into PostgreSQL records.

```python
from ravi.capabilities.history.postgres_history import PostgresHistoryProvider
from ravi.kernel.core.identity import AgentId

mem = PostgresHistoryProvider(
    db_url="postgresql+asyncpg://postgres:postgres@localhost:5432/agentdb",
)

agent_id = AgentId(type="assistant", key="helper")

# Appends and reads are executed via async PostgreSQL queries
await mem.append(
    agent_id=agent_id,
    message=msg,
    session_id="session-123",
)
```

---

## Sources

| File | What it owns |
|---|---|
| [`kernel/storage/history.py`](https://github.com/Ravikumarchavva/ravi-engine/blob/main/src/ravi/kernel/storage/history.py) | `HistoryProvider` Protocol |
| [`agents/context/history.py`](https://github.com/Ravikumarchavva/ravi-engine/blob/main/src/ravi/agents/context/history.py) | `InMemoryHistoryProvider` |
| [`capabilities/history/redis_history.py`](https://github.com/Ravikumarchavva/ravi-engine/blob/main/src/ravi/capabilities/history/redis_history.py) | `RedisHistoryProvider` |
| [`capabilities/history/postgres_history.py`](https://github.com/Ravikumarchavva/ravi-engine/blob/main/src/ravi/capabilities/history/postgres_history.py) | `PostgresHistoryProvider` |
