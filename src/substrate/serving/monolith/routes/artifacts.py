"""Artifacts HTTP surface — curated OKF concepts at session and global scope.

Routes:
  GET    /artifacts                      – list (scope, type, tag filters)
  POST   /artifacts                      – create
  GET    /artifacts/{slug}               – read one
  PATCH  /artifacts/{slug}               – edit (also marks human-verified)
  POST   /artifacts/{slug}/promote       – session → global
  DELETE /artifacts/{slug}               – deprecate by default, ?hard=true to erase

Scope is a query parameter rather than a path segment because every handler
needs the same resolution logic (``session`` requires a ``thread_id``,
``global`` does not) and splitting it across two route trees would duplicate
the auth + prefix wiring in each.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from substrate.serving.monolith.dependencies import ServerDependencies, get_ctx
from substrate.serving.monolith.security.deps import AuthClaims, get_current_user

router = APIRouter(prefix="/artifacts", tags=["artifacts"])

Scope = Literal["session", "global"]


class ArtifactOut(BaseModel):
    slug: str
    scope: str
    type: str
    title: str | None = None
    description: str | None = None
    body: str = ""
    tags: list[str] = Field(default_factory=list)
    status: str = "stable"
    resource: str | None = None
    trust: str = "Unverified"
    generated: dict[str, Any] | None = None
    stale_after: str | None = None


class ArtifactCreate(BaseModel):
    type: str = "Memory"
    title: str | None = None
    description: str | None = None
    body: str = ""
    tags: list[str] = Field(default_factory=list)
    resource: str | None = None


class ArtifactPatch(BaseModel):
    title: str | None = None
    description: str | None = None
    body: str | None = None
    tags: list[str] | None = None
    status: str | None = None


def _to_out(slug: str, scope: str, concept: Any) -> ArtifactOut:
    # `concept` is a capabilities-layer OKF Concept, read structurally: the
    # import-linter contract forbids serving/ importing capabilities/, and
    # this module deliberately adds no exception to that list.
    return ArtifactOut(
        slug=slug,
        scope=scope,
        type=concept.type,
        title=concept.title,
        description=concept.description,
        body=concept.body,
        tags=list(concept.tags),
        status=concept.status,
        resource=concept.resource,
        trust=concept.trust_tier,
        generated=concept.generated,
        stale_after=concept.stale_after,
    )


def _require_store(ctx: ServerDependencies) -> Any:
    if ctx.artifact_store is None:
        raise HTTPException(status_code=503, detail="Artifact storage not configured")
    return ctx.artifact_store


def _prefix(
    ctx: ServerDependencies, user: AuthClaims, scope: Scope, thread_id: str | None
) -> str:
    """Resolve the bundle prefix for *scope*, enforcing thread ownership.

    Session scope is keyed by conversation, so a caller passing someone
    else's thread_id would otherwise read their notes — the tenant check is
    what stops that, mirroring how the rest of the conversation tree is
    scoped.
    """
    store = _require_store(ctx)
    if scope == "session":
        if not thread_id:
            raise HTTPException(
                status_code=400, detail="thread_id is required for session scope"
            )
        return store.scope_prefix(user.tenant_id, conversation_id=thread_id)
    return store.scope_prefix(user.tenant_id, user_id=user.sub)


@router.get("", response_model=list[ArtifactOut])
async def list_artifacts(
    scope: Scope = "global",
    thread_id: str | None = None,
    type: str | None = None,
    tag: list[str] | None = Query(default=None),
    include_deprecated: bool = False,
    ctx: ServerDependencies = Depends(get_ctx),
    user: AuthClaims = Depends(get_current_user),
) -> list[ArtifactOut]:
    store = _require_store(ctx)
    prefix = _prefix(ctx, user, scope, thread_id)
    refs = await store.list(
        prefix,
        scope=scope,
        concept_type=type,
        tags=tag,
        include_deprecated=include_deprecated,
    )
    return [_to_out(r.slug, r.scope, r.concept) for r in refs]


@router.post("", response_model=ArtifactOut, status_code=201)
async def create_artifact(
    payload: ArtifactCreate,
    scope: Scope = "global",
    thread_id: str | None = None,
    ctx: ServerDependencies = Depends(get_ctx),
    user: AuthClaims = Depends(get_current_user),
) -> ArtifactOut:
    store = _require_store(ctx)
    prefix = _prefix(ctx, user, scope, thread_id)

    # Authored by a person, so it starts Human-reviewed rather than
    # Unverified — the trust tier should reflect who actually wrote it.
    slug, concept = await store.create(
        prefix,
        concept_type=payload.type,
        title=payload.title,
        description=payload.description,
        body=payload.body,
        tags=list(payload.tags),
        resource=payload.resource,
        author=store.human_actor(user.sub),
    )
    return _to_out(slug, scope, concept)


@router.get("/{slug}", response_model=ArtifactOut)
async def get_artifact(
    slug: str,
    scope: Scope = "global",
    thread_id: str | None = None,
    ctx: ServerDependencies = Depends(get_ctx),
    user: AuthClaims = Depends(get_current_user),
) -> ArtifactOut:
    store = _require_store(ctx)
    concept = await store.get(_prefix(ctx, user, scope, thread_id), slug)
    if concept is None:
        raise HTTPException(status_code=404, detail="Artifact not found")
    return _to_out(slug, scope, concept)


@router.patch("/{slug}", response_model=ArtifactOut)
async def update_artifact(
    slug: str,
    payload: ArtifactPatch,
    scope: Scope = "global",
    thread_id: str | None = None,
    ctx: ServerDependencies = Depends(get_ctx),
    user: AuthClaims = Depends(get_current_user),
) -> ArtifactOut:
    store = _require_store(ctx)
    prefix = _prefix(ctx, user, scope, thread_id)
    # Passing the editor records a verification, which is what lifts an
    # agent-written concept to Human-reviewed.
    concept = await store.update(
        prefix,
        slug,
        title=payload.title,
        description=payload.description,
        body=payload.body,
        tags=payload.tags,
        status=payload.status,
        editor=store.human_actor(user.sub),
    )
    if concept is None:
        raise HTTPException(status_code=404, detail="Artifact not found")
    return _to_out(slug, scope, concept)


@router.post("/{slug}/promote", response_model=ArtifactOut)
async def promote_artifact(
    slug: str,
    thread_id: str,
    ctx: ServerDependencies = Depends(get_ctx),
    user: AuthClaims = Depends(get_current_user),
) -> ArtifactOut:
    """Promote a session artifact into the user's global bundle.

    Only ever session → global: promotion exists to let something outlive
    one conversation, and there is no meaning to the reverse direction.
    """
    store = _require_store(ctx)
    session_prefix = _prefix(ctx, user, "session", thread_id)
    global_prefix = _prefix(ctx, user, "global", None)

    new_slug = await store.promote(session_prefix, global_prefix, slug)
    if new_slug is None:
        raise HTTPException(status_code=404, detail="Artifact not found")
    concept = await store.get(global_prefix, new_slug)
    if concept is None:  # pragma: no cover - written immediately above
        raise HTTPException(status_code=500, detail="Promotion failed")
    return _to_out(new_slug, "global", concept)


@router.delete("/{slug}", status_code=204)
async def delete_artifact(
    slug: str,
    scope: Scope = "global",
    thread_id: str | None = None,
    hard: bool = False,
    ctx: ServerDependencies = Depends(get_ctx),
    user: AuthClaims = Depends(get_current_user),
) -> None:
    """Deprecate by default; ``?hard=true`` erases the document.

    Soft is the default because an invalidated fact still carries history
    worth keeping (see capabilities/artifacts/okf.py) — a hard delete is
    for genuinely unwanted content, not for superseded content.
    """
    store = _require_store(ctx)
    prefix = _prefix(ctx, user, scope, thread_id)
    ok = (
        await store.delete(prefix, slug)
        if hard
        else await store.deprecate(prefix, slug)
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Artifact not found")
