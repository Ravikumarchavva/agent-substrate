"""Compose the agent: pass instances in. There is no registry and no config that picks backends by name."""

from __future__ import annotations

from substrate import ReActAgent, Runtime
from substrate.context import ContextConfig
from substrate.stores import Store
from substrate.tools import AutoApprove, DurableApproval

from ticket_desk.model import DeskModel
from ticket_desk.threads import AuditedThreads
from ticket_desk.tools import close_ticket, lookup_ticket


def build_agent(store: Store, audit: list[str], *, approval: object | None = None) -> ReActAgent:
    return ReActAgent(
        "desk",
        model=DeskModel(),
        tools=[lookup_ticket, close_ticket],
        context=ContextConfig(AuditedThreads(store.threads, audit)),
        approval_handler=approval or AutoApprove(),
        system_instructions="You run a support desk. Use the tools; never guess a ticket's state.",
    )


async def main() -> None:
    audit: list[str] = []
    store = Store.at("./.desk")
    async with Runtime(store) as runtime:
        agent = build_agent(store, audit)
        print((await runtime.run(agent, "What is the state of T-100?", thread="alice")).output)
    await store.aclose()


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
