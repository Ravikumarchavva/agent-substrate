"""04-4 — Multi-Tenant Agent Sessions: Isolation with asyncio.gather()

Demonstrates running multiple fully isolated agent instances concurrently.
Each agent has its own memory — messages from one session never appear in
another, even when they run in the same event loop.

Key design:
  - One UnboundedMemory (or RedisMemory) instance per user session.
  - One AssistantAgent instance per session, constructed from a separate catalog.
  - asyncio.gather() for concurrency — Python's event loop interleaves them.
  - No shared mutable state between agent instances.

Prerequisites: OPENAI_API_KEY set.
"""

import asyncio

from ravi.reasoning.agents.assistant import AssistantAgent
from ravi.fabric.tools.builtin_tools import CalculatorTool, GetCurrentTimeTool
from ravi.adapters.llm.openai.openai_client import OpenAIClient
from ravi.kernel.agent_catalog import AgentCatalog
from ravi.fabric.memory.unbounded import UnboundedMemory

# Infrastructure:
# - OPENAI_API_KEY environment variable required
# - For persistent, cross-process isolation use RedisMemory (see Section 4)


def _make_agent(user_id: str) -> AssistantAgent:
    """Create a fully isolated agent for one user session."""
    from ravi.config import settings

    catalog = AgentCatalog()
    model_name = settings.CHAT_MODEL.split("/")[-1]
    # Each agent gets its own model client and its own memory instance
    catalog.register_model(
        "primary", OpenAIClient(model=model_name, api_key=settings.OPENAI_API_KEY)
    )
    catalog.register_memory("memory", UnboundedMemory())
    for t in [CalculatorTool(), GetCurrentTimeTool()]:
        catalog.register_tool(t)

    return AssistantAgent(
        name=f"agent-{user_id}",
        description="Helpful assistant",
        catalog=catalog,
        system_instructions=(
            f"You are a personal assistant for user '{user_id}'. "
            "Answer helpfully and concisely."
        ),
        max_iterations=5,
    )


async def main() -> None:

    # ---
    # Section 1: Create multiple isolated agent instances, each with own memory

    agents = {
        "alice": _make_agent("alice"),
        "bob": _make_agent("bob"),
        "carol": _make_agent("carol"),
    }

    print("=== Section 1: Agents created with independent memory instances ===")
    for uid, agent in agents.items():
        print(f"  {uid}: {agent.name!r}, memory id={id(agent.memory)}")

    # ---
    # Section 2: Run all sessions concurrently with asyncio.gather()

    print("\n=== Section 2: Concurrent runs ===")

    async def run_session(user_id: str, query: str) -> str:
        result = await agents[user_id].run(query)
        return result.output_text

    results = await asyncio.gather(
        run_session("alice", "What is 17 * 18?"),
        run_session("bob", "What is the current UTC time?"),
        run_session("carol", "What is 2 ** 10 minus 24?"),
    )

    for user_id, answer in zip(agents, results):
        print(f"  {user_id}: {answer[:120]}")

    # ---
    # Section 3: Memory isolation — prove sessions don't share context

    print("\n=== Section 3: Memory isolation ===")

    # Alice sets a personal fact
    await agents["alice"].run("My favourite colour is crimson. Remember it.")

    # Bob runs a completely different query in parallel
    await agents["bob"].run("My favourite number is 42. Remember it.")

    # Verify isolation: ask each agent about the other's fact
    alice_knows_bob = await agents["alice"].run(
        "What is Bob's favourite number? Answer with just the number or 'unknown'."
    )
    bob_knows_alice = await agents["bob"].run(
        "What is Alice's favourite colour? Answer with just the colour or 'unknown'."
    )

    print(
        f"  Alice asked about Bob's number  -> {alice_knows_bob.output_text.strip()!r}"
    )
    print(
        f"  Bob asked about Alice's colour  -> {bob_knows_alice.output_text.strip()!r}"
    )
    print("  (Both should be 'unknown' — sessions are isolated.)")

    # ---
    # Section 4: Production pattern — user_id → agent configuration
    #
    # In a real multi-tenant API you would keep an agent registry keyed by
    # user/session ID and use RedisMemory for persistence across process
    # restarts:
    #
    #   from ravi.adapters.memory.redis_memory import RedisMemory
    #
    #   REDIS_URL = "redis://localhost:6379/0"
    #
    #   def make_persistent_agent(user_id: str) -> AssistantAgent:
    #       catalog = AgentCatalog()
    #       catalog.register_model("primary", OpenAIClient(model="gpt-4o"))
    #       # session_id scopes all Redis keys to this user
    #       catalog.register_memory(
    #           "default",
    #           RedisMemory(session_id=f"user:{user_id}", redis_url=REDIS_URL),
    #       )
    #       ...
    #       return AssistantAgent(name=f"agent-{user_id}", ...)
    #
    #   # In your FastAPI handler:
    #   async def chat(user_id: str, message: str):
    #       agent = agent_registry.get(user_id) or agent_registry.setdefault(
    #           user_id, make_persistent_agent(user_id)
    #       )
    #       result = await agent.run(message)
    #       return result.output_text
    #
    # Each user_id maps to a dedicated agent with its own isolated Redis keys.
    # The agent instance can be cached in memory or reconstructed from Redis on
    # each request — both patterns work because memory persistence is in Redis.

    print("\n=== Section 4: Production pattern (see comments in source) ===")
    print(
        "  Use RedisMemory(session_id=f'user:{user_id}') for cross-process isolation."
    )
    print("  Cache agent instances in a dict or reconstruct per request.")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
