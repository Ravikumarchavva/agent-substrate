"""Channel — one ordered log many parties write to, read with a cursor each.

A thread is a run's own record; a channel is shared. A group chat, an agent-to-agent
conversation, anywhere several writers speak into one sequence and each reader needs
"what is new since I last looked". The store owns the sequencing and the cursors; the
agents own what to say.

Attention
---------
Every member has a ``Mode``, and an append wakes a member only if its mode says the entry
deserves a look — decided here, cheaply, before any model runs. Why it woke is the wake's
``reason`` (``WakeReason``):

* ``DIRECT``  — the entry is for it: it @mentions it, says @everyone, or replies to its own entry.
* ``ENGAGED`` — it is not for it, but the member was addressed or spoke within the channel's
  ``engage_s`` and is still part of that conversation, as a person who was just talked to stays in it.
* ``AMBIENT`` — nothing to do with it; it only listens to everything.

``ALL`` is woken by every entry from anyone else (as ``AMBIENT`` when nothing else applies); ``MENTIONS``
by ``DIRECT``, and by ``ENGAGED`` while it is part of a conversation; ``MUTED`` only when named.
Being named, replied to or addressed to everyone, or speaking, begins the engagement window.

A wake is a small message in the member's inbox (``data = {channel, seq, reason}``); a burst
becomes one run because the member is already active for the later ones. What to read is
always the log after the member's cursor, never the wake message.

Coordination
------------
``read_up_to`` makes a post a compare-and-set: it is refused (``stale``) if someone else
spoke after the ``seq`` the poster had read, so parallel deciders see each other's reply
and reconsider instead of all saying the same thing.

``breaker`` bounds a conversation that no human is part of. Every entry has a ``depth``: how many agent
entries lead up to it with no person or fresh start between. A person's entry, and an agent's ``fresh`` one
(a timer woke it, not someone speaking), starts again at 0; any other agent entry is one deeper than the entry
it answers (``cause_seq``, by default the latest message). When an entry would be deeper than ``breaker`` the
channel pauses with a system entry until a person speaks, so agents cannot talk to each other forever, while
a daily routine report never trips it.

People
------
A channel's members are *participants*: agents, people, system helpers and people on another network
(``ParticipantKind``). Whoever speaks is the participant; whoever is woken is its ``inbox``, a separate actor
(an agent's chat actor, a bridge's outbox). A person has no inbox: an entry never wakes or starts a run for one,
it only waits to be read. Every member has a read cursor (``cursor``) and a delivered cursor (``delivered``),
and remembers the first entry it may read (``joined_seq``).

Entries are never rewritten in a way that loses what was said. An edit keeps the previous text in its marker;
a reaction, an edit and a delete are each an entry of their own with their own ``seq``, so reading a channel
``after`` a position is enough to hear about all of them. Only the person who wrote an entry can edit or delete
it, and a delete (a person's right to erase) blanks the text and what is attached, everywhere it was kept.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import Field

from substrate.types.content import JsonObject, KernelModel
from substrate.types.identity import Actor


class Mode(StrEnum):
    ALL = "all"
    MENTIONS = "mentions"
    MUTED = "muted"


class WakeReason(StrEnum):
    DIRECT = "direct"
    ENGAGED = "engaged"
    AMBIENT = "ambient"


class ParticipantKind(StrEnum):
    HUMAN = "human"
    """A person using the product: has no inbox and is never woken."""
    AGENT = "agent"
    SYSTEM = "system"
    """A helper that works for the channel (an indexer, a summariser)."""
    EXTERNAL = "external"
    """A person on another network, reached through a bridge: counts as a person, woken only through the bridge's inbox."""


class EntryKind(StrEnum):
    MESSAGE = "message"
    SYSTEM = "system"
    EDIT = "edit"
    """Marks that ``reply_to`` was edited: ``text`` is the new text, ``data["previous"]`` the one it replaced."""
    REACTION = "reaction"
    """Marks that a participant reacted to ``reply_to`` with ``text`` (empty: took the reaction back)."""
    TOMBSTONE = "tombstone"
    """Marks that ``reply_to`` was deleted by its sender."""


SPOKEN = (EntryKind.MESSAGE, EntryKind.SYSTEM)
"""The kinds a reader sees as the conversation; the others only say what happened to an entry."""


EVERYONE = "everyone"
"""The mention that addresses every member."""


def mentions_in(
    text: str, names: Mapping[str, str], *, exclude: str | None = None
) -> list[str]:
    """The addresses ``@Name`` in ``text`` points at (``names`` maps address → name), and ``EVERYONE``
    for ``@everyone``. Case does not matter; ``exclude`` is an address that is never mentioned (the speaker)."""
    lowered = text.casefold()
    found = [EVERYONE] if re.search(r"@everyone\b", lowered) else []
    for address, name in names.items():
        if address != exclude and re.search(
            rf"@{re.escape(name.casefold())}(?!\w)", lowered
        ):
            found.append(address)
    return found


class Member(KernelModel):
    agent: Actor
    """The participant: whoever speaks as this member and is mentioned as it."""
    mode: Mode = Mode.ALL
    cursor: int = -1
    """The last ``seq`` this member has read; ``-1`` before it has read anything."""
    kind: ParticipantKind = ParticipantKind.AGENT
    inbox: Actor | None = None
    """Whom an entry for this member wakes. An agent or system helper without one wakes itself; a person never has one."""
    delivered: int = -1
    """The last ``seq`` that reached this member's device (a person's receipt, shown as a second tick)."""
    joined_seq: int = 0
    """The first ``seq`` this member may read: what was said before it joined is not its to read."""


class ChannelEntry(KernelModel):
    channel: str
    seq: int
    id: str = ""
    """A stable id, the same however the entry is reached; a repeated append with the same key has the same one."""
    sender: Actor
    kind: EntryKind = EntryKind.MESSAGE
    text: str = ""
    mentions: tuple[str, ...] = ()
    """Addresses (``str(Actor)``) of mentioned members, or ``EVERYONE``."""
    reply_to: int | None = None
    caused_by: str | None = None
    """What made the sender speak: a run id, an entry ``seq``, a webhook. Free-form, for tracing."""
    cause_seq: int | None = None
    """The entry this one answers, when that is not the one just before it."""
    depth: int = 0
    """Agent entries leading up to this one with no person or fresh start between (see the module docstring)."""
    data: JsonObject = Field(default_factory=dict)
    """Anything structured that rides with the entry (attachments, say); the channel does not look inside."""
    at: datetime
    edited_at: datetime | None = None
    deleted_at: datetime | None = None


class ChannelChange(KernelModel):
    """What an observer hears after an entry commits: where to read it, not the entry itself."""

    channel: str
    seq: int
    id: str
    kind: EntryKind
    sender: Actor


ChannelObserver = Callable[[ChannelChange], Awaitable[None]]


class ChannelHead(KernelModel):
    """A channel as one of its members sees it in a list: the latest thing said and how much of it is unread."""

    channel: str
    latest: ChannelEntry | None = None
    """The latest message or system notice (not an edit, reaction or delete marker)."""
    unread: int = 0
    """Messages from others after this member's read cursor; system notices, edits and reactions do not count."""
    cursor: int = -1


class AppendResult(KernelModel):
    seq: int | None = None
    """The new entry's ``seq``; ``None`` if it was not appended."""
    id: str | None = None
    """The new entry's id (the earlier one's, for a repeated key)."""
    stale: bool = False
    """Refused because others spoke after ``read_up_to``; ``latest`` says how far the log runs."""
    paused: bool = False
    """Refused because the channel's breaker is tripped; a human message resumes it."""
    latest: int = -1
    woken: tuple[Actor, ...] = ()


@runtime_checkable
class ChannelStore(Protocol):
    async def channel_open(
        self,
        channel: str,
        *,
        tenant: str = "default",
        members: Sequence[Member] = (),
        breaker: int = 40,
        engage_s: float = 600.0,
    ) -> None:
        """Create the channel, or leave it as it is if it exists (members are added, not replaced). ``engage_s`` is how long
        a member keeps following a conversation it was drawn into (see ``WakeReason.ENGAGED``)."""
        ...

    async def channel_configure(
        self, channel: str, *, breaker: int | None = None, engage_s: float | None = None
    ) -> None:
        """Change an open channel's breaker or engagement window; what is not given is left as it is."""
        ...

    async def channel_set_member(self, channel: str, member: Member) -> None:
        """Add a member, or change its mode (its cursor is kept)."""
        ...

    async def channel_remove_member(self, channel: str, agent: Actor) -> None: ...

    async def channel_members(self, channel: str) -> list[Member]: ...

    async def channel_append(
        self,
        channel: str,
        *,
        sender: Actor,
        text: str,
        mentions: Sequence[str] = (),
        reply_to: int | None = None,
        caused_by: str | None = None,
        read_up_to: int | None = None,
        kind: EntryKind = EntryKind.MESSAGE,
        dedup_key: str | None = None,
        data: JsonObject | None = None,
        cause_seq: int | None = None,
        fresh: bool = False,
    ) -> AppendResult:
        """Append an entry, advance the sender's own cursor past it, and wake the members it
        concerns, all in one transaction. With a ``dedup_key``, a repeat of an append that already
        landed returns that entry's ``seq`` and appends nothing: what lets a replayed run post once.

        ``cause_seq`` is the entry this one answers (default: the latest message) and ``fresh`` says nothing
        led to it (a timer fired), so it starts a new chain: both only matter for the ``depth`` the breaker counts.
        A member whose budget is used up is not woken, and the channel says so once."""
        ...

    async def channel_edit(
        self, channel: str, seq: int, sender: Actor, text: str
    ) -> int | None:
        """Replace the text of ``seq``, which only its sender may do, on a message not yet deleted. Appends an ``EDIT`` marker
        (keeping the text it replaced) and returns the marker's ``seq``; ``None`` if it is not allowed. Wakes no one."""
        ...

    async def channel_tombstone(
        self, channel: str, seq: int, sender: Actor
    ) -> int | None:
        """Delete ``seq`` for good, which only its sender may do: its text and attachments are blanked, as is everything an edit
        kept of it. Appends a ``TOMBSTONE`` marker and returns its ``seq``; ``None`` if it is not allowed. Wakes no one."""
        ...

    async def channel_react(
        self, channel: str, seq: int, participant: Actor, emoji: str
    ) -> int | None:
        """Set ``participant``'s one reaction to ``seq`` (``""`` takes it back). Appends a ``REACTION`` marker and returns its
        ``seq``; a repeat of what is already set changes nothing and returns the earlier marker's. ``None`` if ``seq`` does not exist."""
        ...

    async def channel_reactions(
        self, channel: str, seqs: Sequence[int]
    ) -> dict[int, dict[str, str]]:
        """``{seq: {participant address: emoji}}`` for the entries that have reactions."""
        ...

    async def channel_heads(
        self, channels: Sequence[str], participant: Actor
    ) -> list[ChannelHead]:
        """How each of ``channels`` looks in ``participant``'s list: latest message and unread count. Channels that do not exist, or that
        ``participant`` is not in, are left out. One query however many channels."""
        ...

    async def channel_mark_delivered(
        self, channel: str, participant: Actor, upto: int
    ) -> None:
        """Move a member's delivered cursor forward to ``upto`` (never backward)."""
        ...

    def channel_observe(self, observer: ChannelObserver) -> Callable[[], None]:
        """Call ``observer`` after every entry that commits (messages, markers and system notices; not refused posts or repeats),
        in ``seq`` order per channel. An observer that fails is logged and skipped; it never fails the append. Returns a function
        that removes it. How a gateway pushes to its clients."""
        ...

    async def channel_read(
        self, channel: str, *, after: int = -1, limit: int = 200
    ) -> list[ChannelEntry]: ...

    async def channel_last(self, channel: str, limit: int = 1) -> list[ChannelEntry]:
        """The latest ``limit`` entries, oldest first."""
        ...

    async def channel_read_before(
        self, channel: str, before: int, limit: int = 50
    ) -> list[ChannelEntry]:
        """The ``limit`` entries just before ``before`` (``seq < before``), oldest first: a page back through a long conversation."""
        ...

    async def channel_wait(self, channel: str, after: int, timeout_s: float) -> bool:
        """Return once the channel has an entry beyond ``after`` (``True``), or after ``timeout_s`` (``False``): what a
        reader holds open instead of polling. May return early; the caller reads to find out."""
        ...

    async def channel_delete(self, channel: str) -> None:
        """Remove the channel with its entries and members. Runs already woken finish on their own."""
        ...

    async def channel_mark_read(self, channel: str, agent: Actor, upto: int) -> None:
        """Move a member's cursor forward to ``upto`` (never backward)."""
        ...


__all__ = [
    "AppendResult",
    "ChannelChange",
    "ChannelEntry",
    "ChannelHead",
    "ChannelObserver",
    "ChannelStore",
    "EVERYONE",
    "EntryKind",
    "Member",
    "Mode",
    "ParticipantKind",
    "SPOKEN",
    "WakeReason",
    "mentions_in",
]
