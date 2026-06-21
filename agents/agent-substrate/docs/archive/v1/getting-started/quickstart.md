# Quickstart

This is the shortest useful Ravi program.

```python
import asyncio

from agent_substrate.agents import ReActAgent, Runtime
from agent_substrate.agents.context import ContextConfig, InMemoryHistoryProvider
from agent_substrate.integrations.llm.openai import OpenAIClient
from agent_substrate.kernel import Message, ChatPayload, ChatMessage, Role, TextBlock
from agent_substrate.kernel.core.identity import AgentId


async def main() -> None:
    # 1. Initialize the LLM client and the ReActAgent
    client = OpenAIClient(api_key="sk-...", model="gpt-4o")
    agent = ReActAgent(
        "DemoBot",
        model=client,
        context=ContextConfig(InMemoryHistoryProvider()),
    )

    # 2. Run the agent via the Runtime
    async with Runtime() as rt:
        await rt.register(agent)
        
        # 3. Submit a message and await completion
        msg = Message(
            target=agent.id,
            sender=AgentId(type="proxy", key="user"),
            payload=ChatPayload(
                message=ChatMessage(
                    role=Role.USER,
                    content=[TextBlock(text="Why do agents need runtimes?")]
                )
            ),
        )
        run_id = await rt.submit(agent.id, msg)
        
        async for entry in rt.event_log.tail(run_id):
            if entry.kind in ("run.completed", "run.failed", "run.cancelled"):
                break
        
        # 4. Extract and print the final assistant turn response
        history = await agent.history.get_messages(agent.id, session_id=run_id)
        for m in reversed(history):
            if m.role == Role.ASSISTANT:
                print("Response:", " ".join(b.text for b in m.content if isinstance(b, TextBlock)))
                break


if __name__ == "__main__":
    asyncio.run(main())
```

## What happened

1. `OpenAIClient` translates Ravi messages into provider API calls.
2. `UnboundedMemory` stores the conversation in process.
3. `ReActAgent` runs a bounded Think → Act → Observe loop.
4. Since there are no tools here, the model produces a direct answer.

## What to do next

- Add tools with [Create A Tool](../tutorials/create-tool.md)
- Learn the lifecycle in [Agent Lifecycle](../concepts/agent-lifecycle.md)
- Move to the durable runtime in [First Durable Run](first-runtime.md)