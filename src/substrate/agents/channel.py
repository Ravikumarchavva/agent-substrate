"""An agent that lives in channels: it is woken by new entries, reads what it has not read, and chooses whether to speak.

The channel decides *who is woken* (``substrate.runtime.channel``); this decides *what to do about it*. A wake is only a
nudge — the member waits a moment so a burst becomes one wake, reads everything after its cursor as one digest, runs its
ordinary ReAct turn on it, and posts the answer unless it chose silence (``PASS``). If someone else spoke while it was
thinking, its post is refused as stale and it reconsiders with what is new, a bounded number of times.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from substrate.agents.react import ReActAgent
from substrate.agents.routed import handle
from substrate.runtime.channel import EVERYONE, ChannelEntry, EntryKind, mentions_in
from substrate.runtime.message import ChatPayload, DataPayload, Message
from substrate.types import ChatMessage, Role, TextBlock

if TYPE_CHECKING:
    from substrate.runtime.context import RunContext

PASS = "[PASS]"
"""What a member answers with when it has nothing to add: nothing is posted."""


@dataclass(frozen=True)
class ChannelMemberConfig:
    names: Mapping[str, str]
    """Everyone in the channel, ``str(Actor)`` → the name the others know them by."""
    scope: Mapping[str, Any] = field(default_factory=dict)
    """Whose run this is (``user_id``, ``tenant_id``, ``workspace_id``…), as message metadata; a wake carries none."""
    debounce_s: float = 1.5
    rethinks: int = 2


class ChannelMemberAgent(ReActAgent):
    def __init__(self, *args: Any, channel: ChannelMemberConfig, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._config = channel
        self._answers: dict[str, str] = {}

    async def _deliver(  # type: ignore[override]
        self, ctx: RunContext, src_msg: Message, result: dict[str, Any], **kwargs: Any
    ) -> None:
        """A turn over a digest answers into the channel, not back to whoever woke it."""
        if src_msg.metadata.get("channel"):
            self._answers[ctx.run_id] = str(result.get("text", ""))
            return
        await ReActAgent._deliver(ctx, src_msg, result, **kwargs)

    @handle(DataPayload)
    async def _on_data(self, ctx: RunContext, msg: Message) -> None:  # type: ignore[override]
        channel = msg.payload.data.get("channel")  # type: ignore[union-attr]
        if not isinstance(channel, str):
            await ReActAgent._handle_message(self, ctx, msg)
            return
        if self._config.debounce_s:
            await asyncio.sleep(self._config.debounce_s)
        for _ in range(self._config.rethinks + 1):
            entries = await ctx.read_channel(channel)
            if not entries:
                return
            answer = (await self._think(ctx, msg, channel, entries)).strip()
            if not answer or PASS in answer:
                return
            posted = await ctx.post(
                channel,
                answer,
                mentions=self._mentions(answer),
                read_up_to=entries[-1].seq,
            )
            if not posted.stale:
                return

    async def _think(
        self, ctx: RunContext, wake: Message, channel: str, entries: list[ChannelEntry]
    ) -> str:
        turn = Message(
            target=self.id,
            sender=wake.sender,
            payload=ChatPayload(
                message=ChatMessage(
                    role=Role.USER, content=[TextBlock(text=self._digest(entries))]
                )
            ),
            correlation_id=self.id.key,
            metadata={**self._config.scope, "channel": channel},
        )
        await ReActAgent._handle_message(self, ctx, turn)
        return self._answers.pop(ctx.run_id, "")

    def _name(self, address: str) -> str:
        return self._config.names.get(address, address)

    def _digest(self, entries: list[ChannelEntry]) -> str:
        me = str(self.id)
        lines = ["New in the group:"]
        for e in entries:
            if e.kind is EntryKind.SYSTEM:
                lines.append(f"#{e.seq} (note) {e.text}")
                continue
            to = ""
            if me in e.mentions:
                to = " (to you)"
            elif EVERYONE in e.mentions:
                to = " (to everyone)"
            elif e.mentions:
                to = f" (to {', '.join(self._name(a) for a in e.mentions)})"
            re_ = f" (replying to #{e.reply_to})" if e.reply_to is not None else ""
            lines.append(f"#{e.seq} {self._name(str(e.sender))}{re_}{to}: {e.text}")
        lines.append(f"\nReply as yourself, or answer exactly {PASS} to stay silent.")
        return "\n".join(lines)

    def _mentions(self, text: str) -> list[str]:
        return mentions_in(text, self._config.names, exclude=str(self.id))


__all__ = ["ChannelMemberAgent", "ChannelMemberConfig", "PASS"]
