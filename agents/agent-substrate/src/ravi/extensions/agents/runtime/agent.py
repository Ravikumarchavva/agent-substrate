"""RuntimeAgent — backward-compatible alias for ActorAgent.

``ActorAgent`` (``kernel/agents/actor.py``) is now the canonical base class.
``RuntimeAgent`` extends it with no changes to preserve backward compatibility
for any external code that subclasses ``RuntimeAgent`` directly.

New code should subclass ``ActorAgent`` or one of the concrete extensions:
- ``AssistantAgent`` — full LLM cognitive loop
- ``UserProxyAgent`` — bridges external callers into the fabric

Usage::

    class GreeterAgent(RuntimeAgent):
        async def on_message(self, ctx: MessageContext, content: list[ContentBlock]) -> object:
            text = content[0].text if content else ""
            return f"Hello, {text}!"

    runtime = LocalRuntime()
    await runtime.start()
    greeter = GreeterAgent(name="greeter", runtime=runtime)
    await greeter.start()

    result = await runtime.send_message("World", recipient=greeter.id)
    # result == "Hello, World!"
"""

from __future__ import annotations
from ravi.logger import setup_logging

from ravi.kernel.agents.actor import ActorAgent
from ravi.kernel.messages.content import ContentBlock
from ravi.kernel.runtime._contracts import MessageContext

logger = setup_logging()


class RuntimeAgent(ActorAgent):
    """Thin subclass of ``ActorAgent`` kept for backward compatibility.

    Adds a log line on start/stop and raises ``NotImplementedError`` for
    ``on_message()`` so subclasses get a clear error message.
    """

    async def start(self) -> None:
        await super().start()
        logger.info("RuntimeAgent '%s' started (id=%s)", self.name, self.id)

    async def stop(self) -> None:
        await super().stop()
        logger.info("RuntimeAgent '%s' stopped", self.name)

    async def on_message(
        self, ctx: MessageContext, content: list[ContentBlock]
    ) -> object:
        raise NotImplementedError(
            f"{self.__class__.__name__}.on_message() not implemented"
        )
