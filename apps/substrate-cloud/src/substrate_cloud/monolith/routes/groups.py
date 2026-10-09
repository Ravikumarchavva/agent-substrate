"""Groups — ``/groups``: the user and several of their agents in one conversation, and ``/agents/{id}/contacts``: who an agent may message.

Every agent in a group sees every message and chooses whether to reply. ``@Name`` addresses one, ``@everyone`` all of them; a member set to
"mentions" only considers what is addressed to it, and a "muted" one only what names it.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from substrate.runtime import ChannelHead, EntryKind
from substrate.types import Actor
from substrate_cloud.document_reader import document_reader
from substrate_cloud.monolith.dependencies import ServerDependencies, get_ctx
from substrate_cloud.monolith.models import Group
from substrate_cloud.monolith.security.deps import AuthClaims, get_current_user
from substrate_cloud.monolith.security.rls_deps import get_tenant_scoped_db
from substrate_cloud.monolith.services import avatar, pins, transcription
from substrate_cloud.monolith.services.groups import files as group_files
from substrate_cloud.monolith.services.groups.drives import delete_workspace_files
from substrate_cloud.monolith.services.groups import service as groups
from substrate_cloud.monolith.services.agents.service import get_owned_agent

router = APIRouter(tags=["groups"])


class MemberIn(BaseModel):
    agent_id: uuid.UUID
    mode: str = groups.DEFAULT_MODE


class GroupIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    members: List[MemberIn] = Field(min_length=1)


class GroupPatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=60)
    token_cap: Optional[int] = Field(default=None, ge=1000, le=groups.MAX_TOKEN_CAP)
    # Dollars the agents may spend here; ``0`` removes the limit.
    budget_usd: Optional[float] = Field(default=None, ge=0, le=100_000)
    # How many messages in a row agents may add without a person before the group pauses.
    breaker: Optional[int] = Field(default=None, ge=4, le=200)


class MemberOut(BaseModel):
    agent_id: uuid.UUID
    name: str
    role: str
    mode: str
    avatar: Optional[str] = None
    # What this agent has used in this group.
    tokens_used: int = 0
    cost_usd: float = 0.0


class GroupOut(BaseModel):
    id: uuid.UUID
    name: str
    created_at: datetime
    updated_at: datetime
    members: List[MemberOut]
    # The last thing said, one line, and how many entries the user has not seen.
    last_message: Optional[str] = None
    last_sender: Optional[str] = None
    unread: int = 0
    paused: bool = False
    # Members thinking about it right now, for "Scout is typing…" in a list.
    working: List[str] = []
    # What the agents have used in this group and the most they may.
    tokens_used: int = 0
    token_cap: int = 0
    budget_usd: Optional[float] = None
    cost_usd: float = 0.0
    breaker: int = 0
    avatar: Optional[str] = None
    pinned_at: Optional[datetime] = None


class AttachmentOut(BaseModel):
    name: str
    size: int
    mime: str
    # Storage key; fetch it from /files/object?key=…
    key: str
    # The start of its text, which the agents are shown; absent for a picture or a file with no text.
    excerpt: Optional[str] = None
    truncated: bool = False
    # A picture of the first page, for a PDF, and how many pages it has.
    preview_key: Optional[str] = None
    pages: Optional[int] = None
    modified: Optional[float] = None
    # What a recording says, so the agents can read it.
    transcript: Optional[str] = None


class AttachmentIn(BaseModel):
    key: str
    name: str
    size: int = 0
    mime: str = "application/octet-stream"
    excerpt: Optional[str] = None
    truncated: bool = False
    preview_key: Optional[str] = None
    pages: Optional[int] = None
    transcript: Optional[str] = None


class ReactionOut(BaseModel):
    emoji: str
    sender_id: str
    sender: str
    from_user: bool


class EntryOut(BaseModel):
    seq: int
    id: str
    sender_id: str
    sender: str
    from_user: bool
    # ``message`` and ``system`` are what was said; ``edit``, ``reaction`` and ``tombstone`` say what happened to the entry in ``reply_to``
    # (the new text, the emoji, a delete), so reading after a position is enough to hear about all of it.
    kind: str
    text: str
    mentions: List[str]
    reply_to: Optional[int] = None
    attachments: List[AttachmentOut] = []
    at: datetime
    edited_at: Optional[datetime] = None
    deleted_at: Optional[datetime] = None
    reactions: List[ReactionOut] = []


class MessagesOut(BaseModel):
    entries: List[EntryOut]
    latest: int
    # Members thinking about it right now, for "Scout is typing…".
    working: List[str] = []
    # The latest entry every agent in the group has read: the user's messages up to here show as seen by all.
    read_by_all: int = -1


class SendIn(BaseModel):
    text: str = Field(default="", max_length=8000)
    reply_to: Optional[int] = None
    attachments: List[AttachmentIn] = Field(default_factory=list, max_length=10)


class ModeIn(BaseModel):
    mode: str


class ReadIn(BaseModel):
    upto: int


class EditIn(BaseModel):
    text: str = Field(min_length=1, max_length=8000)


class ReactionIn(BaseModel):
    # One emoji, or empty to take the reaction back.
    emoji: str = Field(max_length=16)


class ContactIn(BaseModel):
    agent_id: uuid.UUID
    note: str = Field(default="", max_length=300)


class ContactsIn(BaseModel):
    contacts: List[ContactIn]


class ContactOut(BaseModel):
    agent_id: uuid.UUID
    name: str
    role: str
    note: str


def _check_mode(mode: str) -> str:
    if mode not in groups.MODES:
        raise HTTPException(
            422, f"Mode must be one of: {', '.join(sorted(groups.MODES))}."
        )
    return mode


async def _chats_changed(request: Request, user: AuthClaims) -> None:
    """Tell the person's open feeds (here, or on any other process) to reload their conversations: one was made, left or changed who is in it."""
    await request.app.state.feed_relay.publish({"t": "chats", "user": user.sub})


def _store(ctx: ServerDependencies):
    if ctx.runtime is None:
        raise HTTPException(503, "The runtime is not available.")
    return ctx.runtime.store


async def _owned(db: AsyncSession, group_id: uuid.UUID, user: AuthClaims) -> Group:
    group = await groups.get_owned_group(db, group_id, user)
    if group is None:
        raise HTTPException(404, "Group not found.")
    return group


async def _out(db: AsyncSession, store, group: Group, head: ChannelHead | None = None) -> GroupOut:
    members = await groups.group_members(db, group.id)
    names = await groups.roster(db, group)
    mine = str(groups.user_actor(group.user_identifier))
    if head is None:
        head = next(iter(await store.channel_heads([group.channel], groups.user_actor(group.user_identifier))), None)
    last = head.latest if head else None
    return GroupOut(
        id=group.id,
        name=group.name,
        created_at=group.created_at,
        # The list orders by when something last happened: a rename, or the latest thing said.
        updated_at=max(group.updated_at, last.at) if last else group.updated_at,
        members=[await _member_out(store, group, m, a) for m, a in members],
        last_message=preview_line(last)[:140] if last else None,
        last_sender=names.get(str(last.sender), "Group") if last else None,
        unread=head.unread if head else 0,
        paused=bool(last and last.kind.value == "system"),
        working=[
            names[str(a)]
            for a in await store.working(
                [Actor.from_str(x) for x in names if x != mine]
            )
        ],
        tokens_used=(channel_spend := await groups.spent(store, f"channel:{group.channel}")).tokens,
        token_cap=group.token_cap,
        budget_usd=group.budget_usd,
        cost_usd=channel_spend.cost_usd,
        breaker=group.breaker,
        avatar=group.avatar_key,
        pinned_at=group.pinned_at,
    )


async def _member_out(store, group: Group, member, agent) -> MemberOut:
    spend = await groups.spent(store, f"agent:{groups.member_actor(agent.id, group.id)}")
    return MemberOut(
        agent_id=agent.id, name=agent.name, role=agent.role, mode=member.mode, avatar=agent.avatar_key, tokens_used=spend.tokens, cost_usd=spend.cost_usd
    )


def preview_line(entry) -> str:
    """One line for a list: what was said, or the files when that is all there was."""
    if entry.text.strip():
        return entry.text
    files = entry.data.get("attachments", [])
    if len(files) == 1:
        return f"📎 {files[0].get('name', 'file')}"
    return f"📎 {len(files)} files" if files else ""


def entry_out(e, names: dict[str, str], mine: str, reactions: Optional[dict[str, str]] = None) -> EntryOut:
    return EntryOut(
        seq=e.seq,
        id=e.id,
        sender_id=str(e.sender),
        sender=names.get(str(e.sender), "Group"),
        from_user=str(e.sender) == mine,
        kind=e.kind.value,
        text=e.text,
        mentions=[names.get(m, m) for m in e.mentions],
        reply_to=e.reply_to,
        attachments=[AttachmentOut(**a) for a in e.data.get("attachments", [])],
        at=e.at,
        edited_at=e.edited_at,
        deleted_at=e.deleted_at,
        reactions=[
            ReactionOut(emoji=emoji, sender_id=who, sender=names.get(who, "Group"), from_user=who == mine)
            for who, emoji in (reactions or {}).items()
        ],
    )


@router.get("/groups", response_model=List[GroupOut])
async def list_my_groups(
    db: AsyncSession = Depends(get_tenant_scoped_db),
    user: AuthClaims = Depends(get_current_user),
    ctx: ServerDependencies = Depends(get_ctx),
):
    store = _store(ctx)
    mine = await groups.list_groups(db, user)
    heads = {h.channel: h for h in await store.channel_heads([g.channel for g in mine], groups.user_actor(user.sub))}
    return [await _out(db, store, g, heads.get(g.channel)) for g in mine]


@router.post("/groups", response_model=GroupOut, status_code=201)
async def create_group(
    body: GroupIn,
    request: Request,
    db: AsyncSession = Depends(get_tenant_scoped_db),
    user: AuthClaims = Depends(get_current_user),
    ctx: ServerDependencies = Depends(get_ctx),
):
    store = _store(ctx)
    try:
        group = await groups.create_group(
            db,
            store,
            user,
            body.name.strip(),
            [(m.agent_id, _check_mode(m.mode)) for m in body.members],
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    await db.commit()
    await groups.refresh_members(db, ctx.runtime, group.id)
    await _chats_changed(request, user)
    return await _out(db, store, group)


@router.get("/groups/{group_id}", response_model=GroupOut)
async def get_group(
    group_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_scoped_db),
    user: AuthClaims = Depends(get_current_user),
    ctx: ServerDependencies = Depends(get_ctx),
):
    return await _out(db, _store(ctx), await _owned(db, group_id, user))


@router.patch("/groups/{group_id}", response_model=GroupOut)
async def rename_group(
    group_id: uuid.UUID,
    body: GroupPatch,
    db: AsyncSession = Depends(get_tenant_scoped_db),
    user: AuthClaims = Depends(get_current_user),
    ctx: ServerDependencies = Depends(get_ctx),
):
    group = await _owned(db, group_id, user)
    if body.name is not None:
        group.name = body.name.strip()
    if body.token_cap is not None:
        await groups.set_token_cap(_store(ctx), group, body.token_cap)
    if body.budget_usd is not None:
        await groups.set_budget(_store(ctx), group, body.budget_usd or None)
    if body.breaker is not None:
        group.breaker = body.breaker
        await _store(ctx).channel_configure(group.channel, breaker=body.breaker)
    await db.commit()
    await db.refresh(group)  # updated_at is set by the database on update
    await groups.refresh_members(db, ctx.runtime, group.id)
    return await _out(db, _store(ctx), group)


@router.put("/groups/{group_id}/avatar", response_model=GroupOut)
async def set_group_avatar(
    group_id: uuid.UUID,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_tenant_scoped_db),
    user: AuthClaims = Depends(get_current_user),
    ctx: ServerDependencies = Depends(get_ctx),
):
    group = await _owned(db, group_id, user)
    store = ctx.files_for(user.tenant_id)
    if store is None:
        raise HTTPException(503, "File storage is not configured.")
    try:
        group.avatar_key = await avatar.replace(
            store, user.tenant_id or "default", user.sub, group.workspace_id, group.avatar_key, await file.read(avatar.MAX_UPLOAD_BYTES + 1)
        )
    except avatar.AvatarError as exc:
        raise HTTPException(422, str(exc)) from exc
    await db.commit()
    await db.refresh(group)
    return await _out(db, _store(ctx), group)


@router.delete("/groups/{group_id}/avatar", response_model=GroupOut)
async def clear_group_avatar(
    group_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_scoped_db),
    user: AuthClaims = Depends(get_current_user),
    ctx: ServerDependencies = Depends(get_ctx),
):
    group = await _owned(db, group_id, user)
    store = ctx.files_for(user.tenant_id)
    if store is not None:
        await avatar.remove(store, group.avatar_key)
    group.avatar_key = None
    await db.commit()
    await db.refresh(group)
    return await _out(db, _store(ctx), group)


@router.put("/groups/{group_id}/pin", response_model=GroupOut)
async def pin_group(
    group_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_scoped_db),
    user: AuthClaims = Depends(get_current_user),
    ctx: ServerDependencies = Depends(get_ctx),
):
    group = await _owned(db, group_id, user)
    try:
        await pins.set_pinned(db, group, True)
    except pins.TooManyPinned as exc:
        raise HTTPException(409, str(exc)) from exc
    await db.commit()
    await db.refresh(group)
    return await _out(db, _store(ctx), group)


@router.delete("/groups/{group_id}/pin", response_model=GroupOut)
async def unpin_group(
    group_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_scoped_db),
    user: AuthClaims = Depends(get_current_user),
    ctx: ServerDependencies = Depends(get_ctx),
):
    group = await _owned(db, group_id, user)
    await pins.set_pinned(db, group, False)
    await db.commit()
    await db.refresh(group)
    return await _out(db, _store(ctx), group)


@router.delete("/groups/{group_id}", status_code=204)
async def delete_group(
    group_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_tenant_scoped_db),
    user: AuthClaims = Depends(get_current_user),
    ctx: ServerDependencies = Depends(get_ctx),
):
    group = await _owned(db, group_id, user)
    members = [agent.id for _, agent in await groups.group_members(db, group.id)]
    workspace_id = group.workspace_id
    await _store(ctx).channel_delete(group.channel)
    files = ctx.files_for(user.tenant_id)
    if files is not None:
        await delete_workspace_files(files, user.tenant_id or "default", user.sub, workspace_id)
    await db.delete(group)
    await db.commit()
    await _chats_changed(request, user)
    if ctx.runtime is not None:
        for agent_id in members:
            ctx.runtime.forget(groups.member_actor(agent_id, group_id))
            await groups.refresh_agent(db, ctx.runtime, agent_id)  # it has one folder fewer, and so do the others it shares groups with


@router.get("/groups/{group_id}/messages", response_model=MessagesOut)
async def read_messages(
    group_id: uuid.UUID,
    after: int = -1,
    limit: int = 200,
    wait: float = 0,
    db: AsyncSession = Depends(get_tenant_scoped_db),
    user: AuthClaims = Depends(get_current_user),
    ctx: ServerDependencies = Depends(get_ctx),
):
    group = await _owned(db, group_id, user)
    names = await groups.roster(db, group)
    mine = str(groups.user_actor(group.user_identifier))
    channel = group.channel
    store = _store(ctx)
    if wait > 0:
        # Hold the request open until something is said (or the wait ends) rather than make the client ask again and again. The
        # database connection goes back to the pool meanwhile.
        await db.commit()
        await store.channel_wait(channel, after, min(wait, 3.0))
    entries = await store.channel_read(
        channel, after=after, limit=min(max(limit, 1), 500)
    )
    busy = await store.working([Actor.from_str(a) for a in names if a != mine])
    reactions = await store.channel_reactions(channel, [e.seq for e in entries if e.kind is EntryKind.MESSAGE])
    # What the page has reached the user's device: the second tick for the others' side of a person-to-person chat, and the base for
    # "delivered" generally.
    if entries:
        await store.channel_mark_delivered(channel, groups.user_actor(group.user_identifier), entries[-1].seq)
    return MessagesOut(
        entries=[entry_out(e, names, mine, reactions.get(e.seq)) for e in entries],
        latest=entries[-1].seq if entries else after,
        working=[names[str(a)] for a in busy],
        read_by_all=await groups.read_by_all(store, channel),
    )


@router.post("/groups/{group_id}/files", response_model=AttachmentOut, status_code=201)
async def upload_group_file(
    group_id: uuid.UUID,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_tenant_scoped_db),
    user: AuthClaims = Depends(get_current_user),
    ctx: ServerDependencies = Depends(get_ctx),
):
    """Add a file to the group's shared files; attach it to a message by sending its ``key``."""
    group = await _owned(db, group_id, user)
    store = ctx.files_for(user.tenant_id)
    if store is None:
        raise HTTPException(503, "File storage is not configured.")
    data = await file.read()
    if len(data) > group_files.MAX_FILE_BYTES:
        raise HTTPException(
            413,
            f"A file can be at most {group_files.MAX_FILE_BYTES // (1024 * 1024)} MB.",
        )
    try:
        saved = await group_files.save_upload(
            store,
            group,
            user,
            file.filename or "file",
            data,
            file.content_type,
            document_reader(),
        )
    except Exception as exc:  # noqa: BLE001 - quota and storage errors are the user's to read, not a crash
        if "quota" in type(exc).__name__.lower():
            raise HTTPException(413, str(exc)) from exc
        raise
    if (file.content_type or "").startswith("audio/"):
        # A recording is understood by what it says. If it cannot be transcribed it is shared all the same, as a file.
        try:
            saved["transcript"] = (await transcription.transcribe(ctx, data, file.filename or "audio.webm")).strip() or None
        except transcription.TranscriptionUnavailable:
            saved["transcript"] = None
    return AttachmentOut(**saved)


@router.get("/groups/{group_id}/files", response_model=List[AttachmentOut])
async def list_group_files(
    group_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_scoped_db),
    user: AuthClaims = Depends(get_current_user),
    ctx: ServerDependencies = Depends(get_ctx),
):
    """Everything in the group's shared files: what you uploaded and what the agents made."""
    group = await _owned(db, group_id, user)
    store = ctx.files_for(user.tenant_id)
    if store is None:
        return []
    return [
        AttachmentOut(**f) for f in await group_files.list_files(store, group, user)
    ]


@router.post("/groups/{group_id}/messages", response_model=EntryOut, status_code=201)
async def send_message(
    group_id: uuid.UUID,
    body: SendIn,
    db: AsyncSession = Depends(get_tenant_scoped_db),
    user: AuthClaims = Depends(get_current_user),
    ctx: ServerDependencies = Depends(get_ctx),
):
    group = await _owned(db, group_id, user)
    store = _store(ctx)
    text = body.text.strip()
    if not text and not body.attachments:
        raise HTTPException(422, "Write something or attach a file.")
    attachments = []
    for a in body.attachments:
        if not group_files.belongs(group, user, a.key):
            raise HTTPException(422, f"{a.name} is not one of this group's files.")
        relative = a.key.removeprefix(group_files.shared_prefix(group, user))
        record = group_files.attachment(group, user, relative, a.size, a.mime)
        if a.excerpt:
            # The text the upload read, handed back with the message; it is the sender's own content, clipped to the same size.
            record |= {
                "excerpt": a.excerpt[: group_files.EXCERPT_CHARS],
                "truncated": a.truncated,
            }
        if a.preview_key and group_files.belongs(group, user, a.preview_key):
            record |= {"preview_key": a.preview_key, "pages": a.pages}
        if a.transcript:
            record |= {"transcript": a.transcript[: group_files.EXCERPT_CHARS]}
        attachments.append(record)
    result = await groups.post_as_user(
        db,
        store,
        group,
        user,
        text,
        body.reply_to,
        {"attachments": attachments} if attachments else None,
    )
    if result.seq is None:
        raise HTTPException(409, "The group could not take that message.")
    await db.commit()
    names = await groups.roster(db, group)
    (entry,) = await store.channel_read(group.channel, after=result.seq - 1, limit=1)
    return entry_out(entry, names, str(groups.user_actor(group.user_identifier)))


@router.post("/groups/{group_id}/read", status_code=204)
async def mark_read(
    group_id: uuid.UUID,
    body: ReadIn,
    db: AsyncSession = Depends(get_tenant_scoped_db),
    user: AuthClaims = Depends(get_current_user),
    ctx: ServerDependencies = Depends(get_ctx),
):
    group = await _owned(db, group_id, user)
    await _store(ctx).channel_mark_read(group.channel, groups.user_actor(user.sub), body.upto)


async def _marker(store, group: Group, user: AuthClaims, db: AsyncSession, seq: int | None, what: str) -> EntryOut:
    """The entry that records a change (an edit, a reaction, a delete), as the client folds it into what it shows."""
    if seq is None:
        raise HTTPException(404, f"{what} is not there, or is not yours to change.")
    (marker,) = await store.channel_read(group.channel, after=seq - 1, limit=1)
    return entry_out(marker, await groups.roster(db, group), str(groups.user_actor(user.sub)))


@router.patch("/groups/{group_id}/messages/{seq}", response_model=EntryOut)
async def edit_message(
    group_id: uuid.UUID,
    seq: int,
    body: EditIn,
    db: AsyncSession = Depends(get_tenant_scoped_db),
    user: AuthClaims = Depends(get_current_user),
    ctx: ServerDependencies = Depends(get_ctx),
):
    """Change what you said. The old text is kept with the edit; the agents are not woken by it."""
    group = await _owned(db, group_id, user)
    store = _store(ctx)
    done = await store.channel_edit(group.channel, seq, groups.user_actor(user.sub), body.text.strip())
    return await _marker(store, group, user, db, done, "That message")


@router.delete("/groups/{group_id}/messages/{seq}", response_model=EntryOut)
async def delete_message(
    group_id: uuid.UUID,
    seq: int,
    db: AsyncSession = Depends(get_tenant_scoped_db),
    user: AuthClaims = Depends(get_current_user),
    ctx: ServerDependencies = Depends(get_ctx),
):
    """Take back what you said, for everyone: its text and attachments are blanked wherever they were kept."""
    group = await _owned(db, group_id, user)
    store = _store(ctx)
    done = await store.channel_tombstone(group.channel, seq, groups.user_actor(user.sub))
    return await _marker(store, group, user, db, done, "That message")


@router.put("/groups/{group_id}/messages/{seq}/reaction", response_model=EntryOut)
async def react_to_message(
    group_id: uuid.UUID,
    seq: int,
    body: ReactionIn,
    db: AsyncSession = Depends(get_tenant_scoped_db),
    user: AuthClaims = Depends(get_current_user),
    ctx: ServerDependencies = Depends(get_ctx),
):
    """Your one reaction to a message (an empty emoji takes it back)."""
    group = await _owned(db, group_id, user)
    store = _store(ctx)
    done = await store.channel_react(group.channel, seq, groups.user_actor(user.sub), body.emoji.strip())
    return await _marker(store, group, user, db, done, "That message")


@router.post("/groups/{group_id}/members", response_model=GroupOut, status_code=201)
async def add_member(
    group_id: uuid.UUID,
    body: MemberIn,
    request: Request,
    db: AsyncSession = Depends(get_tenant_scoped_db),
    user: AuthClaims = Depends(get_current_user),
    ctx: ServerDependencies = Depends(get_ctx),
):
    group = await _owned(db, group_id, user)
    store = _store(ctx)
    try:
        await groups.add_member(
            db, store, group, user, body.agent_id, _check_mode(body.mode)
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    await db.commit()
    await groups.refresh_members(db, ctx.runtime, group.id)
    await _chats_changed(request, user)
    return await _out(db, store, group)


@router.patch("/groups/{group_id}/members/{agent_id}", response_model=GroupOut)
async def set_member_mode(
    group_id: uuid.UUID,
    agent_id: uuid.UUID,
    body: ModeIn,
    db: AsyncSession = Depends(get_tenant_scoped_db),
    user: AuthClaims = Depends(get_current_user),
    ctx: ServerDependencies = Depends(get_ctx),
):
    group = await _owned(db, group_id, user)
    store = _store(ctx)
    if not any(a.id == agent_id for _, a in await groups.group_members(db, group.id)):
        raise HTTPException(404, "That agent is not in this group.")
    await groups.add_member(db, store, group, user, agent_id, _check_mode(body.mode))
    await db.commit()
    await groups.refresh_members(db, ctx.runtime, group.id)
    return await _out(db, store, group)


@router.delete("/groups/{group_id}/members/{agent_id}", status_code=204)
async def remove_member(
    group_id: uuid.UUID,
    agent_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_tenant_scoped_db),
    user: AuthClaims = Depends(get_current_user),
    ctx: ServerDependencies = Depends(get_ctx),
):
    group = await _owned(db, group_id, user)
    if len(await groups.group_members(db, group.id)) <= 1:
        raise HTTPException(
            422, "A group needs at least one agent. Delete the group instead."
        )
    await groups.remove_member(db, _store(ctx), group, agent_id)
    await db.commit()
    await _chats_changed(request, user)
    await groups.refresh_members(db, ctx.runtime, group.id)
    await groups.refresh_agent(db, ctx.runtime, agent_id)  # it is not in this group's list now, but it still has its others


# -- contacts ------------------------------------------------------------------------------------------------------------------------


@router.get("/agents/{agent_id}/contacts", response_model=List[ContactOut])
async def get_contacts(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_scoped_db),
    user: AuthClaims = Depends(get_current_user),
):
    agent = await get_owned_agent(db, agent_id, user)
    if agent is None:
        raise HTTPException(404, "Agent not found.")
    return [
        ContactOut(agent_id=a.id, name=a.name, role=a.role, note=c.note)
        for c, a in await groups.contacts_of(db, agent.id)
    ]


@router.put("/agents/{agent_id}/contacts", response_model=List[ContactOut])
async def put_contacts(
    agent_id: uuid.UUID,
    body: ContactsIn,
    db: AsyncSession = Depends(get_tenant_scoped_db),
    user: AuthClaims = Depends(get_current_user),
    ctx: ServerDependencies = Depends(get_ctx),
):
    agent = await get_owned_agent(db, agent_id, user)
    if agent is None:
        raise HTTPException(404, "Agent not found.")
    try:
        await groups.set_contacts(
            db, user, agent, [(c.agent_id, c.note) for c in body.contacts]
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    await db.commit()
    return [
        ContactOut(agent_id=a.id, name=a.name, role=a.role, note=c.note)
        for c, a in await groups.contacts_of(db, agent.id)
    ]
