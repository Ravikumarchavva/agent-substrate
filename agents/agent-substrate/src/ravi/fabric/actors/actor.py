"""ActorAgent — the single kernel contract for all agents.

Every agent in this framework is an actor:
- registered with a runtime (required, never None)
- addressed by an AgentId
- receives messages through on_message()
- communicates only via send() / publish()

There is no standalone agent.run(). External callers enter the fabric through
a UserProxyAgent. The runtime routes everything.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING
from typing import runtime_checkable, Protocol

from ravi.kernel.messages.content import ContentBlock
from ravi.kernel.runtime._identity import AgentId, TopicId
from ravi.kernel.runtime._contracts import MessageContext
from ravi.kernel.runtime._protocol import AgentRuntime
from ravi.kernel.tools.base_tool import BaseTool
from ravi.fabric.catalog import AgentCatalogRegistry

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# StreamChannel — protocol that EventBus (server layer) satisfies via duck typing
# ---------------------------------------------------------------------------


@runtime_checkable
class StreamChannel(Protocol):
    """Anything that can receive a stream of typed events.

    ``server.sse.events.EventBus`` satisfies this protocol without
    inheriting from it — duck typing only.
    """

    async def emit(self, event: object) -> None:
        """Put an event on the channel."""
        ...

    def close(self) -> None:
        """Signal that no more events will be emitted."""
        ...


# ---------------------------------------------------------------------------
# StreamEnvelope — wraps a task with a streaming output channel
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class StreamEnvelope:
    """Message payload that asks an agent to run in streaming mode.

    The agent emits events to ``channel`` as it processes the task,
    then closes the channel when done.  The sender iterates the channel
    asynchronously to consume events without waiting for the agent to finish.

    Usage::

        bus = EventBus()
        envelope = StreamEnvelope(task="what is 2+2?", channel=bus)
        asyncio.create_task(runtime.send_message(envelope, recipient=agent_id))
        async for event in bus:
            ...  # stream tokens to HTTP client
    """

    task: str
    channel: StreamChannel


# ---------------------------------------------------------------------------
# ActorAgent — the one true base class for all agents
# ---------------------------------------------------------------------------


class ActorAgent(ABC):
    """Every agent is an actor registered with a runtime.

    Subclasses override ``on_message()`` to define behavior.
    ``start()`` registers the handler; ``stop()`` tears it down.

    Parameters
    ----------
    name:
        Agent type name — used for registration and routing.
    runtime:
        The ``AgentRuntime`` this agent lives in. Required; there is no
        standalone agent execution.
    key:
        Instance key for ``AgentId``. Use distinct keys when running
        multiple instances of the same agent type.
    description:
        Human-readable description.
    tools:
        Tools available to this agent. Passed to ``catalog`` if provided.
    subscriptions:
        Topics this agent subscribes to on ``start()``.
    catalog:
        Unified capability catalog. Auto-built from ``tools`` if not given.
    """

    def __init__(
        self,
        name: str,
        runtime: AgentRuntime,
        *,
        key: str = "default",
        description: str = "",
        tools: Optional[list[BaseTool]] = None,
        subscriptions: Optional[list[TopicId]] = None,
        catalog: Optional[AgentCatalogRegistry] = None,
    ) -> None:
        self.name = name
        self.runtime = runtime
        self.key = key
        self.description = description or f"{name} agent"
        self.catalog: AgentCatalogRegistry = catalog or AgentCatalogRegistry.from_tools_and_skills(
            tools or []
        )
        self._subscriptions: list[TopicId] = list(subscriptions) if subscriptions else []
        self._started = False

    # -- Identity ------------------------------------------------------------

    @property
    def id(self) -> AgentId:
        """Unique ``AgentId`` for this instance."""
        return AgentId(type=self.name, key=self.key)

    # -- Tool access ---------------------------------------------------------

    @property
    def tools(self) -> list[BaseTool]:
        return self.catalog.all_tools()

    @tools.setter
    def tools(self, value: list[BaseTool]) -> None:
        for t in self.catalog.all_tools():
            self.catalog.unregister(t.name)
        for t in value:
            self.catalog.register_tool(t)

    def get_tool(self, name: str) -> Optional[BaseTool]:
        return self.catalog.get_tool(name)

    # -- Lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        """Register this agent with the runtime and subscribe to topics."""
        if self._started:
            return
        await self.runtime.register(self.name, self._dispatch)
        for topic in self._subscriptions:
            await self.runtime.subscribe(self.name, topic)
        self._started = True

    async def stop(self) -> None:
        """Mark the agent as stopped."""
        self._started = False

    # -- Message contract ----------------------------------------------------

    @abstractmethod
    async def on_message(
        self, ctx: MessageContext, content: list[ContentBlock]
    ) -> object:
        """Handle an incoming message.

        This is the single entry point for all communication.  Override it
        in every subclass.

        Parameters
        ----------
        ctx:
            Routing context: ``ctx.sender``, ``ctx.agent_id``, ``ctx.runtime``.
        content:
            Message payload as a list of ``ContentBlock`` values.
            For ``StreamEnvelope`` messages, ``content[0]`` is the envelope.

        Returns
        -------
        object
            Response sent back to the caller for ``send_message`` (point-to-point).
            Ignored for ``publish_message`` (pub-sub).
        """

    # -- Messaging helpers ---------------------------------------------------

    async def send(
        self,
        message: object,
        *,
        recipient: AgentId,
    ) -> object:
        """Send a point-to-point message and await the response."""
        return await self.runtime.send_message(
            message,
            sender=self.id,
            recipient=recipient,
        )

    async def publish(
        self,
        message: object,
        *,
        topic: TopicId,
    ) -> None:
        """Broadcast a message to all subscribers of ``topic``."""
        await self.runtime.publish_message(
            message,
            sender=self.id,
            topic=topic,
        )

    # -- Internal dispatch ---------------------------------------------------

    async def _dispatch(
        self, ctx: MessageContext, content: list[ContentBlock]
    ) -> object:
        return await self.on_message(ctx, content)

    # -- Repr ----------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"<{self.__class__.__name__}("
            f"name={self.name!r}, key={self.key!r}, "
            f"tools={len(self.tools)}, started={self._started}"
            f")>"
        )
