"""Channel — one ordered log many parties write to, read with a cursor each.

A thread is a run's own record; a channel is shared. A group chat, an agent-to-agent
conversation, anywhere several writers speak into one sequence and each reader needs
"what is new since I last looked". The store owns the sequencing and the cursors; the
agents own what to say.

Attention
---------
Every member has a ``Mode``. An append wakes a member only if its mode says the entry
deserves a deliberation — decided here, cheaply, before any model runs:

* ``ALL``      — every entry from anyone else.
* ``MENTIONS`` — an entry that @mentions it, says @everyone, or replies to its own entry.
* ``MUTED``    — only an entry that @mentions it by name.

A wake is a small message in the member's inbox (``data = {channel, seq}``); a burst
becomes one run because the member is already active for the later ones. What to read is
always the log after the member's cursor, never the wake message.

Coordination
------------
``read_up_to`` makes a post a compare-and-set: it is refused (``stale``) if someone else
spoke after the ``seq`` the poster had read, so parallel deciders see each other's reply
and reconsider instead of all saying the same thing.

``breaker`` bounds a conversation that no human is part of: after that many consecutive
entries from agents, the channel pauses with a system entry until a human speaks.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
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


class EntryKind(StrEnum):
    MESSAGE = "message"
    SYSTEM = "system"


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
    mode: Mode = Mode.ALL
    cursor: int = -1
    """The last ``seq`` this member has read; ``-1`` before it has read anything."""


class ChannelEntry(KernelModel):
    channel: str
    seq: int
    sender: Actor
    kind: EntryKind = EntryKind.MESSAGE
    text: str = ""
    mentions: tuple[str, ...] = ()
    """Addresses (``str(Actor)``) of mentioned members, or ``EVERYONE``."""
    reply_to: int | None = None
    caused_by: str | None = None
    """What made the sender speak: a run id, an entry ``seq``, a webhook. Free-form, for tracing."""
    depth: int = 0
    """Entries in a row, up to this one, spoken by agents with no human between."""
    data: JsonObject = Field(default_factory=dict)
    """Anything structured that rides with the entry (attachments, say); the channel does not look inside."""
    at: datetime


class AppendResult(KernelModel):
    seq: int | None = None
    """The new entry's ``seq``; ``None`` if it was not appended."""
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
    ) -> None:
        """Create the channel, or leave it as it is if it exists (members are added, not replaced)."""
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
    ) -> AppendResult:
        """Append an entry, advance the sender's own cursor past it, and wake the members it
        concerns, all in one transaction. With a ``dedup_key``, a repeat of an append that already
        landed returns that entry's ``seq`` and appends nothing: what lets a replayed run post once."""
        ...

    async def channel_read(
        self, channel: str, *, after: int = -1, limit: int = 200
    ) -> list[ChannelEntry]: ...

    async def channel_last(self, channel: str, limit: int = 1) -> list[ChannelEntry]:
        """The latest ``limit`` entries, oldest first."""
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
    "ChannelEntry",
    "ChannelStore",
    "EVERYONE",
    "EntryKind",
    "Member",
    "Mode",
    "mentions_in",
]
