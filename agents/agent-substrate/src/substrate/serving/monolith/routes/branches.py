"""Thread branch and checkpoint management endpoints.

Routes:
  GET   /threads/{id}/branches                     – list branches
  POST  /threads/{id}/branches/fork                – fork a new branch
  GET   /threads/{id}/branches/{branch_id}         – get branch details
  GET   /threads/{id}/branches/{branch_id}/messages – get resolved branch message history
  GET   /threads/{id}/branches/{branch_id}/checkpoints – list checkpoints
  POST  /threads/{id}/branches/{branch_id}/checkpoints – save a checkpoint
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from substrate.agents.context.history import DefaultHistoryResolver
from substrate.capabilities.storage.layout import conversation_branch_workspace_prefix
from substrate.kernel.exceptions import (
    BranchAlreadyExistsError,
    BranchHeadConflictError,
    BranchNotFoundError,
    DAGIntegrityError,
)
from substrate.kernel.storage.history import HistoryCheckpoint
from substrate.serving.monolith.dependencies import ServerDependencies, get_ctx
from substrate.serving.monolith.schemas import (
    BranchForkRequest,
    BranchOut,
    CheckpointCreateRequest,
    CheckpointOut,
)
from substrate.serving.monolith.security.deps import AuthClaims, get_current_user
from substrate.serving.monolith.security.rls_deps import get_tenant_scoped_db
from substrate.serving.monolith.services import get_owned_thread

router = APIRouter(
    prefix="/threads/{thread_id}/branches",
    tags=["branches"],
)


@router.get("", response_model=List[BranchOut])
async def list_branches_endpoint(
    thread_id: uuid.UUID,
    ctx: ServerDependencies = Depends(get_ctx),
    db: AsyncSession = Depends(get_tenant_scoped_db),
    user: AuthClaims = Depends(get_current_user),
) -> List[BranchOut]:
    """List all branches stored for this thread."""
    thread = await get_owned_thread(db, thread_id, user)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")

    session_id = str(thread_id)
    branches = []
    if hasattr(ctx.history, "list_branches"):
        branches = await ctx.history.list_branches(session_id)

    # Ensure at least 'main' exists in output
    has_main = any(b.id == "main" for b in branches)
    if not has_main and hasattr(ctx.history, "ensure_branch"):
        main_b = await ctx.history.ensure_branch(session_id, "main")
        branches = [main_b, *branches]

    return [
        BranchOut(
            id=b.id,
            session_id=b.session_id,
            head_message_id=b.head_message_id,
            forked_from_message_id=b.forked_from_message_id,
            version=b.version,
            created_at=b.created_at,
        )
        for b in branches
    ]


@router.post("/fork", response_model=BranchOut, status_code=201)
async def fork_branch_endpoint(
    thread_id: uuid.UUID,
    body: BranchForkRequest,
    ctx: ServerDependencies = Depends(get_ctx),
    db: AsyncSession = Depends(get_tenant_scoped_db),
    user: AuthClaims = Depends(get_current_user),
) -> BranchOut:
    """Fork a new branch pointer and duplicate the branch's workspace snapshot files."""
    thread = await get_owned_thread(db, thread_id, user)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")

    session_id = str(thread_id)

    if not hasattr(ctx.history, "fork_branch"):
        raise HTTPException(status_code=501, detail="History provider does not support branching")

    # Ensure source branch exists before forking if it was implicit
    if hasattr(ctx.history, "ensure_branch"):
        await ctx.history.ensure_branch(session_id, body.source_branch_id)

    target_fork_node_id = body.fork_from_message_id
    if target_fork_node_id and hasattr(ctx.history, "get_node"):
        try:
            node = await ctx.history.get_node(target_fork_node_id)
            if node is None or node.session_id != session_id:
                # If target node not found directly (e.g. client ID), fall back to source branch head
                source_branch = await ctx.history.get_branch(session_id, body.source_branch_id)
                target_fork_node_id = source_branch.head_message_id if source_branch else None
        except Exception:
            target_fork_node_id = None

    try:
        new_branch = await ctx.history.fork_branch(
            session_id,
            source_branch_id=body.source_branch_id,
            new_branch_id=body.new_branch_id,
            fork_from_message_id=target_fork_node_id,
        )
    except BranchAlreadyExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except BranchNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except DAGIntegrityError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Synchronize physical workspace files for the new branch
    if ctx.file_store is not None and hasattr(ctx.file_store, "copy_prefix"):
        src_prefix = conversation_branch_workspace_prefix(
            user.tenant_id, user.sub, session_id, body.source_branch_id
        )
        dst_prefix = conversation_branch_workspace_prefix(
            user.tenant_id, user.sub, session_id, body.new_branch_id
        )
        try:
            await ctx.file_store.copy_prefix(src_prefix, dst_prefix)
        except Exception:
            pass  # Non-blocking if workspace has no files yet

    return BranchOut(
        id=new_branch.id,
        session_id=new_branch.session_id,
        head_message_id=new_branch.head_message_id,
        forked_from_message_id=new_branch.forked_from_message_id,
        version=new_branch.version,
        created_at=new_branch.created_at,
    )


@router.get("/{branch_id}", response_model=BranchOut)
async def get_branch_endpoint(
    thread_id: uuid.UUID,
    branch_id: str,
    ctx: ServerDependencies = Depends(get_ctx),
    db: AsyncSession = Depends(get_tenant_scoped_db),
    user: AuthClaims = Depends(get_current_user),
) -> BranchOut:
    """Get branch pointer details by ID."""
    thread = await get_owned_thread(db, thread_id, user)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")

    session_id = str(thread_id)
    if not hasattr(ctx.history, "get_branch"):
        raise HTTPException(status_code=501, detail="History provider does not support branching")

    branch = await ctx.history.get_branch(session_id, branch_id)
    if not branch and branch_id == "main" and hasattr(ctx.history, "ensure_branch"):
        branch = await ctx.history.ensure_branch(session_id, "main")

    if not branch:
        raise HTTPException(status_code=404, detail=f"Branch '{branch_id}' not found")

    return BranchOut(
        id=branch.id,
        session_id=branch.session_id,
        head_message_id=branch.head_message_id,
        forked_from_message_id=branch.forked_from_message_id,
        version=branch.version,
        created_at=branch.created_at,
    )


@router.get("/{branch_id}/messages")
async def get_branch_messages_endpoint(
    thread_id: uuid.UUID,
    branch_id: str,
    ctx: ServerDependencies = Depends(get_ctx),
    db: AsyncSession = Depends(get_tenant_scoped_db),
    user: AuthClaims = Depends(get_current_user),
) -> List[Dict[str, Any]]:
    """Return chronological messages resolved along this branch's ancestry DAG."""
    thread = await get_owned_thread(db, thread_id, user)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")

    session_id = str(thread_id)
    if not hasattr(ctx.history, "get_branch"):
        raise HTTPException(status_code=501, detail="History provider does not support branching")

    branch = await ctx.history.get_branch(session_id, branch_id)
    if not branch and branch_id == "main" and hasattr(ctx.history, "ensure_branch"):
        branch = await ctx.history.ensure_branch(session_id, "main")

    if not branch:
        raise HTTPException(status_code=404, detail=f"Branch '{branch_id}' not found")

    if branch.head_message_id is None:
        return []

    resolver = DefaultHistoryResolver(ctx.history)
    nodes = await resolver.resolve_ancestry(branch.head_message_id)

    return [
        {
            "id": node.id,
            "parent_id": node.parent_id,
            "role": node.payload.role,
            "content": [b.model_dump() for b in node.payload.content],
            "text": node.payload.text,
            "created_at": node.created_at.isoformat(),
        }
        for node in nodes
    ]


@router.get("/{branch_id}/checkpoints", response_model=List[CheckpointOut])
async def list_checkpoints_endpoint(
    thread_id: uuid.UUID,
    branch_id: str,
    ctx: ServerDependencies = Depends(get_ctx),
    db: AsyncSession = Depends(get_tenant_scoped_db),
    user: AuthClaims = Depends(get_current_user),
) -> List[CheckpointOut]:
    """List all checkpoints stored for this thread."""
    thread = await get_owned_thread(db, thread_id, user)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")

    session_id = str(thread_id)
    if not hasattr(ctx.history, "list_checkpoints"):
        return []

    checkpoints = await ctx.history.list_checkpoints(session_id)
    return [
        CheckpointOut(
            id=cp.id,
            session_id=cp.session_id,
            anchor_message_id=cp.anchor_message_id,
            summary=cp.summary,
            state=cp.state,
            parent_checkpoint_id=cp.parent_checkpoint_id,
            created_at=cp.created_at,
        )
        for cp in checkpoints
    ]


@router.post("/{branch_id}/checkpoints", response_model=CheckpointOut, status_code=201)
async def create_checkpoint_endpoint(
    thread_id: uuid.UUID,
    branch_id: str,
    body: CheckpointCreateRequest,
    ctx: ServerDependencies = Depends(get_ctx),
    db: AsyncSession = Depends(get_tenant_scoped_db),
    user: AuthClaims = Depends(get_current_user),
) -> CheckpointOut:
    """Manually persist a compaction checkpoint anchor."""
    thread = await get_owned_thread(db, thread_id, user)
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")

    session_id = str(thread_id)
    if not hasattr(ctx.history, "save_checkpoint"):
        raise HTTPException(status_code=501, detail="History provider does not support checkpoints")

    cp = HistoryCheckpoint(
        id=uuid4().hex,
        session_id=session_id,
        anchor_message_id=body.anchor_message_id,
        summary=body.summary,
        state=body.state or {},
    )

    try:
        await ctx.history.save_checkpoint(cp)
    except DAGIntegrityError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return CheckpointOut(
        id=cp.id,
        session_id=cp.session_id,
        anchor_message_id=cp.anchor_message_id,
        summary=cp.summary,
        state=cp.state,
        parent_checkpoint_id=cp.parent_checkpoint_id,
        created_at=cp.created_at,
    )

