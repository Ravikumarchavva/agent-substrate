"""An agent that lives in channels: it is woken by new entries, reads what it has not read, and chooses whether to speak.

The channel decides *who is woken* (``substrate.runtime.channel``); this decides *what to do about it*. A wake is only a
nudge — the member waits a moment so a burst becomes one wake, reads everything after its cursor as one digest, runs its
ordinary ReAct turn on it, and posts the answer unless it chose silence (``PASS``). If someone else spoke while it was
thinking, its post is refused as stale and it reconsiders with what is new, a bounded number of times.

How much thought a wake deserves depends on *why* it came (``WakeReason``), as a person's does. When the message is for
the member (``DIRECT``) it takes a full turn. When it is not (``ENGAGED``: it is following a conversation; ``AMBIENT``: it
only overhears), it first takes a quick, cheap look (``ChannelMemberConfig.triage``) to decide whether it has anything to
add, and a member that is busy elsewhere (``availability``) does not look at all and leaves it unread for later. Only what a
member actually said becomes its history: a pass, or an answer that went stale, is dropped.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
import logging
from typing import TYPE_CHECKING, Any

from substrate.agents.react import ReActAgent
from substrate.agents.routed import handle
from substrate.runtime.channel import EVERYONE, ChannelEntry, EntryKind, WakeReason, mentions_in
from substrate.runtime.message import ChatPayload, DataPayload, Message
from substrate.models import Modality
from substrate.models.protocols import ChatModel, GenerationOptions
from substrate.tools import ToolExecutionResult, ToolRisk
from substrate.types import ChatMessage, MediaBlock, Role, TextBlock
from substrate.types.content import ContentBlock

if TYPE_CHECKING:
    from substrate.runtime.context import RunContext

MediaLoader = Callable[[Mapping[str, Any]], Awaitable[MediaBlock | None]]
"""Opens one shared attachment as media the model can be shown (the host knows where files are kept), or ``None`` when it cannot."""
Publisher = Callable[[str], Awaitable[Mapping[str, Any]]]
"""Makes the file at a path in the member's sandbox shareable and returns its attachment record; raises ``ValueError`` saying why not."""

logger = logging.getLogger(__name__)

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
    media: MediaLoader | None = None
    """How pictures shared in the channel reach the model. Without it, a picture is only named."""
    max_media: int = 4
    """The most pictures put in front of the model in one digest: each is paid for by every member that looks."""
    publish: Publisher | None = None
    """How a member shares a file it made. Without it the member has no ``attach`` tool."""
    triage: ChatModel | None = None
    """A cheap model for the quick look at a message that is not for the member. Without it, every wake takes a full turn."""
    availability: Callable[[], Awaitable[bool]] | None = None
    """Whether the member can attend to chatter now (``False``: it is busy elsewhere). A message for it is answered regardless."""
    roles: Mapping[str, str] = field(default_factory=dict)
    """What each member does, by name: so the quick look knows who an unaddressed question is for."""
    recall: int = 40
    """How many earlier entries a full turn is shown after a quick look has consumed them."""


class AttachTool:
    """Share a file with the channel: it is attached to the member's next message."""

    name = "attach"
    description = (
        "Share a file with the group by its path (for example /groups/trip/chart.png or /workspace/report.pdf). "
        "It is attached to your reply, so everyone can open it. Make the file first, then attach it."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "The file's path in your sandbox."}},
        "required": ["path"],
        "additionalProperties": False,
    }
    risk = ToolRisk.SAFE
    idempotent = True

    def __init__(self, share: Callable[[Any, str], Awaitable[Mapping[str, Any]]]) -> None:
        self._share = share

    async def execute(self, *, ctx: Any = None, path: str = "", **_: Any) -> ToolExecutionResult:
        try:
            attachment = await self._share(ctx, path)
        except ValueError as exc:
            return ToolExecutionResult(name=self.name, content=[TextBlock(text=f"Could not share {path}: {exc}")], is_error=True)
        return ToolExecutionResult(
            name=self.name, content=[TextBlock(text=f"{attachment.get('name', path)} will be attached to your reply.")]
        )


class ChannelMemberAgent(ReActAgent):
    def __init__(self, *args: Any, channel: ChannelMemberConfig, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if channel.publish is not None:
            # The member's tools are its own (the factory builds a toolbox per agent), so it may add the one that shares files.
            from substrate.tools.toolbox import Toolbox

            if self.tools is None:
                self.tools = Toolbox()
            self.tools.add(AttachTool(self._share))
        self._config = channel
        self._answers: dict[str, str] = {}
        self._outbox: dict[str, list[dict[str, Any]]] = {}
        self._drafts: dict[str, tuple[Any, ...]] = {}

    async def _share(self, ctx: Any, path: str) -> Mapping[str, Any]:
        assert self._config.publish is not None
        attachment = await self._config.publish(path)
        self._outbox.setdefault(ctx.run_id, []).append(dict(attachment))
        return attachment

    async def _persist(  # type: ignore[override]
        self, ctx: RunContext, session_id: str, messages: list[ChatMessage], n_loaded: int, branch_id: str
    ) -> None:
        """A turn is held as a draft until its answer is posted (``_keep``): a pass, or an answer that went stale, is not history. History keeps
        a note of each picture, not its bytes: every later turn loads history, and would pay for the picture again."""
        kept = [*messages[:n_loaded], *(_without_media(m) for m in messages[n_loaded:])]
        self._drafts[ctx.run_id] = (ctx, session_id, kept, n_loaded, branch_id)

    async def _keep(self, ctx: RunContext) -> None:
        """The member spoke: its latest turn is now history."""
        if (draft := self._drafts.pop(ctx.run_id, None)) is not None:
            await super()._persist(*draft)

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
        for_me = msg.payload.data.get("reason", WakeReason.DIRECT) == WakeReason.DIRECT  # type: ignore[union-attr]
        logger.info("%s: woken in %s, reason %s", self._name(str(self.id)), channel, msg.payload.data.get("reason"))  # type: ignore[union-attr]
        if not for_me and self._config.availability is not None and not await self._config.availability():
            logger.info("%s: busy elsewhere, leaving the channel unread", self._name(str(self.id)))
            return  # busy elsewhere: what was said stays unread, and is there when it next looks
        if self._config.debounce_s:
            await asyncio.sleep(self._config.debounce_s)
        decided = for_me or self._config.triage is None
        earlier: list[ChannelEntry] = []
        context_loaded = False
        try:
            for _ in range(self._config.rethinks + 1):
                entries = await ctx.read_channel(channel)
                if not entries:
                    return
                if not context_loaded:
                    # What was said just before is part of what the new messages mean ("what is that place?" after a picture).
                    earlier = await ctx.recall_channel(channel, before=entries[0].seq, limit=self._config.recall)
                    context_loaded = True
                if not decided:
                    if not await self._has_something_to_add(ctx, entries, earlier, str(msg.payload.data.get("reason"))):  # type: ignore[union-attr]
                        return
                    decided = True
                answer = (await self._think(ctx, msg, channel, entries, earlier)).strip()
                files = self._outbox.get(ctx.run_id, [])
                logger.info("%s: full turn answered %r", self._name(str(self.id)), answer[:60])
                if (not answer or PASS in answer) and not files:
                    return
                text = "" if PASS in answer else answer
                posted = await ctx.post(
                    channel,
                    text,
                    mentions=self._mentions(text),
                    read_up_to=entries[-1].seq,
                    data={"attachments": files} if files else None,
                )
                if not posted.stale:
                    await self._keep(ctx)
                    return
        finally:
            self._outbox.pop(ctx.run_id, None)
            self._drafts.pop(ctx.run_id, None)

    def _line(self, e: ChannelEntry) -> str:
        to = f" (to {', '.join('everyone' if a == EVERYONE else self._name(a) for a in e.mentions)})" if e.mentions else ""
        shared = "".join(f" [shared {a.get('name')}]" for a in e.data.get("attachments", []))
        return f"#{e.seq} {self._name(str(e.sender))}{to}: {e.text}{shared}"

    async def _has_something_to_add(
        self, ctx: RunContext, entries: list[ChannelEntry], earlier: list[ChannelEntry], reason: str
    ) -> bool:
        """The quick, cheap look: is there anything here this member should speak to? Only for what is not addressed to it."""
        assert self._config.triage is not None
        me = self._name(str(self.id))
        roster = ", ".join(
            f"{name} ({self._config.roles[name]})" if self._config.roles.get(name) else name for name in self._config.names.values()
        )
        following = (
            "You were part of this conversation a moment ago: continue it if what was just said follows on from it."
            if reason == WakeReason.ENGAGED
            else "Nobody addressed you by name."
        )
        digest = "\n".join(self._line(e) for e in entries if e.kind is EntryKind.MESSAGE)
        before = "\n".join(self._line(e) for e in earlier if e.kind is EntryKind.MESSAGE)
        prompt = (
            f"You are {me}, in a group chat with: {roster}.\n{following} Decide whether to reply to the NEW messages below.\n"
            "SPEAK if a new message asks a question or makes a request that nobody has answered since it was sent and that you are able to help "
            "with (if several members could, SPEAK anyway: the others see your answer and stay quiet).\n"
            "PASS only if it is meant for someone else, was already answered after it was sent, or is just chatter that needs no reply.\n\n"
            + (f"Said just before:\n{before}\n\n" if before else "")
            + f"New messages:\n{digest}\n\nAnswer with exactly one word: SPEAK or PASS."
        )
        response = await ctx.llm(
            [ChatMessage(role=Role.USER, content=[TextBlock(text=prompt)])],
            options=GenerationOptions(system_instructions="You decide quickly whether to join a conversation."),
            client=self._config.triage,
        )
        verdict = response.text.strip()
        logger.info("%s: quick look (%s) at #%s-#%s says %r", me, reason, entries[0].seq, entries[-1].seq, verdict[:40])
        return verdict.upper().startswith("SPEAK")

    async def _think(
        self, ctx: RunContext, wake: Message, channel: str, entries: list[ChannelEntry], earlier: list[ChannelEntry]
    ) -> str:
        turn = Message(
            target=self.id,
            sender=wake.sender,
            payload=ChatPayload(
                message=ChatMessage(
                    role=Role.USER, content=await self._digest(entries, earlier)
                )
            ),
            correlation_id=self.id.key,
            metadata={**self._config.scope, "channel": channel},
        )
        await ReActAgent._handle_message(self, ctx, turn)
        return self._answers.pop(ctx.run_id, "")

    def _name(self, address: str) -> str:
        return self._config.names.get(address, address)

    def _hears_or_sees(self, modality: Modality) -> bool:
        capabilities = getattr(self.model, "capabilities", None)
        return capabilities is not None and modality in capabilities.input_modalities

    async def _digest(self, entries: list[ChannelEntry], earlier: list[ChannelEntry] = ()) -> list[ContentBlock]:  # type: ignore[assignment]
        """What is new, as the model is shown it: the lines of text, with each picture it can see in its place. ``earlier`` is what was said
        just before, already seen by the member's quick look but not by its full turn: shown first, as context, without its pictures."""
        me = str(self.id)
        blocks: list[ContentBlock] = []
        lines: list[str] = []
        shown = 0
        def flush() -> None:
            if lines:
                blocks.append(TextBlock(text="\n".join(lines)))
                lines.clear()

        if earlier:
            lines.append("(earlier, already seen)")
            # A picture shared earlier is often what the new message is about: the newest few are shown again, in their place.
            pictures = [
                id(a)
                for e in earlier
                for a in e.data.get("attachments", [])
                if str(a.get("mime") or "").startswith("image/")
            ][-max(1, self._config.max_media // 2) :]
            can_show = self._config.media is not None and self._hears_or_sees(Modality.IMAGE)
            for e in earlier:
                if e.kind is not EntryKind.MESSAGE:
                    continue
                lines.append(self._line(e))
                for a in e.data.get("attachments", []):
                    if can_show and id(a) in pictures and (block := await self._config.media(a)) is not None:
                        flush()
                        blocks.append(block)
                        shown += 1
            lines.append("")
        lines.append("New in the group:")

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
            for a in e.data.get("attachments", []):
                lines.append(f"    (attached: {a.get('name')}, {a.get('size', 0)} bytes)")
                mime = str(a.get("mime") or "")
                if mime.startswith("image/") and self._config.media is not None:
                    if not self._hears_or_sees(Modality.IMAGE):
                        lines.append("    (a picture: you cannot see it)")
                    elif shown >= self._config.max_media:
                        lines.append("    (a picture: not shown, there are too many in this message)")
                    elif (block := await self._config.media(a)) is not None:
                        flush()
                        blocks.append(block)
                        shown += 1
                    else:
                        lines.append("    (a picture: it could not be opened)")
                elif mime.startswith("audio/"):
                    if a.get("transcript"):
                        lines.append(f"    said: {a['transcript']}")
                    else:
                        lines.append("    (a voice note that could not be transcribed)")
                elif a.get("excerpt"):
                    more = " [only the start is shown]" if a.get("truncated") else ""
                    body = "\n".join("    | " + row for row in str(a["excerpt"]).splitlines())
                    lines.append(f"    contents of {a.get('name')}{more}:\n{body}")
                else:
                    lines.append("    (no text could be read from it)")
        lines.append(f"\nReply as yourself, or answer exactly {PASS} to stay silent.")
        flush()
        return blocks

    def _mentions(self, text: str) -> list[str]:
        return mentions_in(text, self._config.names, exclude=str(self.id))


def _without_media(message: ChatMessage) -> ChatMessage:
    """The message with each picture or recording replaced by a note of its name."""
    if not any(isinstance(b, MediaBlock) for b in message.content):
        return message
    content = [
        TextBlock(text=f"[{b.type} shared: {b.filename or 'unnamed'}]") if isinstance(b, MediaBlock) else b
        for b in message.content
    ]
    return message.model_copy(update={"content": content})


__all__ = ["AttachTool", "ChannelMemberAgent", "ChannelMemberConfig", "MediaLoader", "PASS", "Publisher"]
