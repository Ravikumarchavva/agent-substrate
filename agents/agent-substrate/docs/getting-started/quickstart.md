# Quickstart

This is the shortest useful Raavan program.

```python
import asyncio

from raavan.core.agents.react_agent import ReActAgent
from raavan.core.memory import UnboundedMemory
from raavan.integrations.llm.openai.openai_client import OpenAIClient


async def main() -> None:
    client = OpenAIClient(api_key="sk-...", model="gpt-4o")
    memory = UnboundedMemory()
    agent = ReActAgent(model_client=client, memory=memory, tools=[])

    reply = await agent.run("Summarize why agent runtimes need tool execution")
    print(reply)


asyncio.run(main())
```

## What happened

1. `OpenAIClient` translates Raavan messages into provider API calls.
2. `UnboundedMemory` stores the conversation in process.
3. `ReActAgent` runs a bounded Think → Act → Observe loop.
4. Since there are no tools here, the model produces a direct answer.

## What to do next

- Add tools with [Create A Tool](../tutorials/create-tool.md)
- Learn the lifecycle in [Agent Lifecycle](../concepts/agent-lifecycle.md)
- Move to the durable runtime in [First Durable Run](first-runtime.md)