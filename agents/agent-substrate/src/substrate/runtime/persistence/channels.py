"""Channels over the runtime's SQL tables: the sequencing, cursors and attention of
``substrate.runtime.channel``, in the same transactions as the inbox they wake."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime, timezone
from typing import Any

from substrate.runtime.channel import (
    EVERYONE,
    AppendResult,
    ChannelEntry,
    EntryKind,
    Member,
    Mode,
    WakeReason,
)
from substrate.runtime.message import DataPayload, Message
from substrate.runtime.persistence.accounts import exhausted
from substrate.runtime.store import Delivery
from substrate.stores.database import Database, Row, Tx
from substrate.types.content import JsonObject
from substrate.types.identity import Actor

_SCHEMA_CHANNELS = """
CREATE TABLE IF NOT EXISTS rt_channels (
    channel TEXT PRIMARY KEY,
    tenant TEXT NOT NULL DEFAULT 'default',
    breaker INTEGER NOT NULL,
    next_seq INTEGER NOT NULL DEFAULT 0,
    streak INTEGER NOT NULL DEFAULT 0,
    paused INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS rt_channel_members (
    channel TEXT NOT NULL,
    member TEXT NOT NULL,
    mode TEXT NOT NULL,
    cursor INTEGER NOT NULL DEFAULT -1,
    PRIMARY KEY (channel, member)
);

CREATE TABLE IF NOT EXISTS rt_accounts (
    account TEXT PRIMARY KEY,
    tokens BIGINT NOT NULL DEFAULT 0,
    cost_micros BIGINT NOT NULL DEFAULT 0,
    turns BIGINT NOT NULL DEFAULT 0,
    max_tokens BIGINT,
    max_cost_micros BIGINT,
    max_turns BIGINT
);

CREATE TABLE IF NOT EXISTS rt_run_accounts (
    run_id TEXT NOT NULL,
    account TEXT NOT NULL,
    PRIMARY KEY (run_id, account)
);

CREATE TABLE IF NOT EXISTS rt_channel_entries (
    channel TEXT NOT NULL,
    seq INTEGER NOT NULL,
    sender TEXT NOT NULL,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    mentions_json TEXT NOT NULL,
    reply_to INTEGER,
    caused_by TEXT,
    depth INTEGER NOT NULL DEFAULT 0,
    at DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (channel, seq)
);
"""


def _schema_dedup(database: Database) -> str:
    """A repeated append with the same key lands once (``ctx.post`` across replays)."""
    exists = "IF NOT EXISTS " if database.dialect == "postgresql" else ""
    return (
        f"ALTER TABLE rt_channel_entries ADD COLUMN {exists}dedup_key TEXT;\n"
        "CREATE UNIQUE INDEX IF NOT EXISTS rt_channel_entries_dedup_idx "
        "ON rt_channel_entries (channel, dedup_key) WHERE dedup_key IS NOT NULL;"
    )


def _schema_entry_data(database: Database) -> str:
    """What rides with an entry (attachments): kept as JSON text beside it."""
    exists = "IF NOT EXISTS " if database.dialect == "postgresql" else ""
    return f"ALTER TABLE rt_channel_entries ADD COLUMN {exists}data_json TEXT;"


def _schema_engagement(database: Database) -> str:
    """How long a member follows a conversation it was drawn into, and until when it still does."""
    exists = "IF NOT EXISTS " if database.dialect == "postgresql" else ""
    return (
        f"ALTER TABLE rt_channels ADD COLUMN {exists}engage_s DOUBLE PRECISION NOT NULL DEFAULT 600;\n"
        f"ALTER TABLE rt_channel_members ADD COLUMN {exists}engaged_until DOUBLE PRECISION;"
    )


_SYSTEM = Actor("system", "channel")


def _entry(row: Row) -> ChannelEntry:
    return ChannelEntry(
        channel=row["channel"],
        seq=row["seq"],
        sender=Actor.from_str(row["sender"]),
        kind=EntryKind(row["kind"]),
        text=row["text"],
        mentions=tuple(json.loads(row["mentions_json"])),
        reply_to=row["reply_to"],
        caused_by=row["caused_by"],
        depth=row["depth"],
        data=json.loads(row["data_json"]) if row["data_json"] else {},
        at=datetime.fromtimestamp(row["at"], tz=timezone.utc),
    )


class Channels:
    """Mixed into ``RuntimeStore``; uses its transaction helper, clock and inbox delivery."""

    _clock: Callable[[], datetime]
    _tx: Callable[[Callable[[Tx], Awaitable[Any]]], Awaitable[Any]]
    _deliver: Callable[..., Awaitable[Any]]
    _channel_events: dict[str, asyncio.Event]

    async def channel_open(
        self,
        channel: str,
        *,
        tenant: str = "default",
        members: Sequence[Member] = (),
        breaker: int = 40,
        engage_s: float = 600.0,
    ) -> None:
        async def do(tx: Tx) -> None:
            await tx.lock(f"channel:{channel}")
            await tx.execute(
                "INSERT INTO rt_channels (channel, tenant, breaker, engage_s) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (channel) DO NOTHING",
                channel,
                tenant,
                breaker,
                engage_s,
            )
            for m in members:
                await self._set_member(tx, channel, m)

        await self._tx(do)

    async def channel_configure(
        self, channel: str, *, breaker: int | None = None, engage_s: float | None = None
    ) -> None:
        async def do(tx: Tx) -> None:
            await tx.lock(f"channel:{channel}")
            if breaker is not None:
                await tx.execute("UPDATE rt_channels SET breaker = ? WHERE channel = ?", breaker, channel)
            if engage_s is not None:
                await tx.execute("UPDATE rt_channels SET engage_s = ? WHERE channel = ?", engage_s, channel)

        await self._tx(do)

    @staticmethod
    async def _set_member(tx: Tx, channel: str, member: Member) -> None:
        await tx.execute(
            "INSERT INTO rt_channel_members (channel, member, mode, cursor) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (channel, member) DO UPDATE SET mode = EXCLUDED.mode",
            channel,
            str(member.agent),
            member.mode.value,
            member.cursor,
        )

    async def channel_set_member(self, channel: str, member: Member) -> None:
        await self._tx(lambda tx: self._set_member(tx, channel, member))

    async def channel_remove_member(self, channel: str, agent: Actor) -> None:
        await self._tx(
            lambda tx: tx.execute(
                "DELETE FROM rt_channel_members WHERE channel = ? AND member = ?",
                channel,
                str(agent),
            )
        )

    async def channel_members(self, channel: str) -> list[Member]:
        async def do(tx: Tx) -> list[Member]:
            rows = await tx.fetchall(
                "SELECT member, mode, cursor FROM rt_channel_members WHERE channel = ? ORDER BY member",
                channel,
            )
            return [
                Member(
                    agent=Actor.from_str(r["member"]),
                    mode=Mode(r["mode"]),
                    cursor=r["cursor"],
                )
                for r in rows
            ]

        return await self._tx(do)

    async def channel_read(
        self, channel: str, *, after: int = -1, limit: int = 200
    ) -> list[ChannelEntry]:
        async def do(tx: Tx) -> list[ChannelEntry]:
            rows = await tx.fetchall(
                "SELECT * FROM rt_channel_entries WHERE channel = ? AND seq > ? ORDER BY seq LIMIT ?",
                channel,
                after,
                limit,
            )
            return [_entry(r) for r in rows]

        return await self._tx(do)

    async def channel_last(self, channel: str, limit: int = 1) -> list[ChannelEntry]:
        async def do(tx: Tx) -> list[ChannelEntry]:
            rows = await tx.fetchall(
                "SELECT * FROM rt_channel_entries WHERE channel = ? ORDER BY seq DESC LIMIT ?",
                channel,
                limit,
            )
            return [_entry(r) for r in reversed(rows)]

        return await self._tx(do)

    async def channel_wait(self, channel: str, after: int, timeout_s: float) -> bool:
        loop = asyncio.get_running_loop()
        end = loop.time() + timeout_s
        while True:
            (last,) = await self.channel_last(channel, 1) or [None]
            if last is not None and last.seq > after:
                return True
            left = end - loop.time()
            if left <= 0:
                return False
            event = self._channel_events.setdefault(channel, asyncio.Event())
            event.clear()
            # Another process's append cannot set this event, so look again every second.
            try:
                await asyncio.wait_for(event.wait(), timeout=min(left, 1.0))
            except asyncio.TimeoutError:
                pass

    async def channel_delete(self, channel: str) -> None:
        async def do(tx: Tx) -> None:
            await tx.lock(f"channel:{channel}")
            for table in ("rt_channel_entries", "rt_channel_members", "rt_channels"):
                await tx.execute(f"DELETE FROM {table} WHERE channel = ?", channel)
            await tx.execute(
                "DELETE FROM rt_accounts WHERE account = ?", f"channel:{channel}"
            )

        await self._tx(do)

    async def channel_mark_read(self, channel: str, agent: Actor, upto: int) -> None:
        await self._tx(
            lambda tx: tx.execute(
                "UPDATE rt_channel_members SET cursor = ? WHERE channel = ? AND member = ? AND cursor < ?",
                upto,
                channel,
                str(agent),
                upto,
            )
        )

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
        async def do(tx: Tx) -> AppendResult:
            await tx.lock(f"channel:{channel}")
            ch = await tx.fetchone(
                "SELECT * FROM rt_channels WHERE channel = ?", channel
            )
            if ch is None:
                raise KeyError(channel)
            latest = ch["next_seq"] - 1
            if dedup_key is not None:
                done = await tx.fetchone(
                    "SELECT seq FROM rt_channel_entries WHERE channel = ? AND dedup_key = ?",
                    channel,
                    dedup_key,
                )
                if done is not None:
                    return AppendResult(seq=done["seq"], latest=latest)
            by_agent = sender.type != "user"  # anything that is not a person: ``agent``, ``member``…
            streak = ch["streak"]
            if by_agent and ch["paused"]:
                return AppendResult(paused=True, latest=latest)
            if read_up_to is not None and await tx.fetchone(
                "SELECT 1 AS x FROM rt_channel_entries WHERE channel = ? AND seq > ? AND sender != ?",
                channel,
                read_up_to,
                str(sender),
            ):
                return AppendResult(stale=True, latest=latest)
            if by_agent and streak + 1 > ch["breaker"]:
                await self._write_entry(
                    tx,
                    channel,
                    ch["next_seq"],
                    _SYSTEM,
                    EntryKind.SYSTEM,
                    f"Paused: agents have spoken {streak} times in a row without a person. "
                    "A new message from a person resumes the conversation.",
                    (),
                    None,
                    None,
                    streak,
                )
                await tx.execute(
                    "UPDATE rt_channels SET paused = 1, next_seq = next_seq + 1 WHERE channel = ?",
                    channel,
                )
                return AppendResult(paused=True, latest=latest + 1)
            streak = streak + 1 if by_agent else 0
            seq = ch["next_seq"]
            await self._write_entry(
                tx,
                channel,
                seq,
                sender,
                kind,
                text,
                tuple(mentions),
                reply_to,
                caused_by,
                streak,
                dedup_key,
                data,
            )
            await tx.execute(
                "UPDATE rt_channels SET next_seq = ?, streak = ?, paused = ? WHERE channel = ?",
                seq + 1,
                streak,
                0 if not by_agent else ch["paused"],
                channel,
            )
            await tx.execute(
                "UPDATE rt_channel_members SET cursor = ? WHERE channel = ? AND member = ? AND cursor < ?",
                seq,
                channel,
                str(sender),
                seq,
            )
            replied_to = None
            if reply_to is not None:
                row = await tx.fetchone(
                    "SELECT sender FROM rt_channel_entries WHERE channel = ? AND seq = ?",
                    channel,
                    reply_to,
                )
                replied_to = row["sender"] if row else None
            woken: list[Actor] = []
            now = self._clock().timestamp()
            window = now + ch["engage_s"]
            # What it is to speak is to be part of the conversation that follows.
            await tx.execute(
                "UPDATE rt_channel_members SET engaged_until = ? WHERE channel = ? AND member = ?",
                window,
                channel,
                str(sender),
            )
            members = await tx.fetchall(
                "SELECT member, mode, engaged_until FROM rt_channel_members WHERE channel = ? AND member != ? ORDER BY member",
                channel,
                str(sender),
            )
            for m in members:
                address = m["member"]
                reason = _wake_reason(
                    Mode(m["mode"]),
                    address,
                    tuple(mentions),
                    replied_to,
                    engaged=(m["engaged_until"] or 0) > now,
                )
                if reason is None:
                    continue
                if reason is WakeReason.DIRECT:
                    await tx.execute(
                        "UPDATE rt_channel_members SET engaged_until = ? WHERE channel = ? AND member = ?",
                        window,
                        channel,
                        address,
                    )
                target = Actor.from_str(address)
                accounts = (
                    f"channel:{channel}",
                    f"agent:{address}",
                    f"tenant:{ch['tenant']}",
                )
                if await exhausted(tx, accounts):
                    continue
                await self._deliver(
                    tx,
                    Delivery(
                        agent=target,
                        msg=Message(
                            id=f"chan:{channel}:{seq}:{address}",
                            target=target,
                            sender=sender,
                            payload=DataPayload(
                                data={"channel": channel, "seq": seq, "reason": reason.value}
                            ),
                            correlation_id=f"chan:{channel}",
                        ),
                        tenant=ch["tenant"],
                        accounts=accounts,
                    ),
                    wake=True,
                )
                woken.append(target)
            return AppendResult(seq=seq, latest=seq, woken=tuple(woken))

        result = await self._tx(do)
        if result.seq is not None and (event := self._channel_events.get(channel)):
            event.set()
        return result

    async def _write_entry(
        self,
        tx: Tx,
        channel: str,
        seq: int,
        sender: Actor,
        kind: EntryKind,
        text: str,
        mentions: tuple[str, ...],
        reply_to: int | None,
        caused_by: str | None,
        depth: int,
        dedup_key: str | None = None,
        data: JsonObject | None = None,
    ) -> None:
        await tx.execute(
            "INSERT INTO rt_channel_entries "
            "(channel, seq, sender, kind, text, mentions_json, reply_to, caused_by, depth, at, dedup_key, data_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            channel,
            seq,
            str(sender),
            kind.value,
            text,
            json.dumps(list(mentions)),
            reply_to,
            caused_by,
            depth,
            self._clock().timestamp(),
            dedup_key,
            json.dumps(data) if data else None,
        )


def _wake_reason(
    mode: Mode,
    address: str,
    mentions: tuple[str, ...],
    replied_to: str | None,
    *,
    engaged: bool,
) -> WakeReason | None:
    """Why an entry should wake a member in ``mode``, or ``None`` for no wake. Cheap on purpose: no model runs to decide it."""
    named = address in mentions
    if mode is Mode.MUTED:
        return WakeReason.DIRECT if named else None
    if named or EVERYONE in mentions or replied_to == address:
        return WakeReason.DIRECT
    if engaged:
        return WakeReason.ENGAGED
    return WakeReason.AMBIENT if mode is Mode.ALL else None
