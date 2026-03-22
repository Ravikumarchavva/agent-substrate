"""Policy Authorization Service — HTTP routes.

POST /policy/check          Check if an action is allowed
GET  /policy/roles/{user}   Get effective role for a user
POST /policy/rules          Create a policy rule (admin)
GET  /policy/rules          List policy rules
POST /policy/grants         Grant workspace role
POST /policy/seed           Seed default policies
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from agent_framework.shared.auth.claims import AuthClaims
from agent_framework.shared.auth.middleware import get_current_user, require_role

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/policy", tags=["policy"])


class PolicyCheckRequest(BaseModel):
    action: str
    resource_type: str = "*"
    resource_id: str = "*"


class PolicyCheckResponse(BaseModel):
    allowed: bool
    action: str
    role: str
    reason: str = ""


class PolicyRuleCreate(BaseModel):
    role: str
    action: str
    resource_type: str = "*"
    effect: str = "allow"
    conditions: Optional[Dict[str, Any]] = None
    priority: int = 0


class WorkspaceGrantCreate(BaseModel):
    user_id: str
    workspace_id: str
    role: str


@router.post("/check", response_model=PolicyCheckResponse)
async def check_policy(
    body: PolicyCheckRequest,
    request: Request,
    user: AuthClaims = Depends(get_current_user),
):
    """Check whether the caller is authorized for a specific action."""
    from agent_framework.services.policy.service import check_permission

    db_factory = request.app.state.session_factory
    async with db_factory() as db:
        allowed = await check_permission(
            db,
            user,
            body.action,
            body.resource_type,
            body.resource_id,
        )
    return PolicyCheckResponse(
        allowed=allowed,
        action=body.action,
        role=user.role,
        reason="granted" if allowed else "denied",
    )


@router.get("/roles/{user_id}")
async def get_role(
    user_id: str,
    tenant_id: str = "default",
    workspace_id: str = "default",
    request: Request = None,
):
    """Get the effective role for a user in a workspace."""
    from agent_framework.services.policy.service import get_effective_role

    db_factory = request.app.state.session_factory
    async with db_factory() as db:
        role = await get_effective_role(db, user_id, tenant_id, workspace_id)
    return {"user_id": user_id, "role": role, "workspace_id": workspace_id}


@router.post("/rules", status_code=201)
async def create_rule(
    body: PolicyRuleCreate,
    request: Request,
    user: AuthClaims = Depends(require_role("platform_admin")),
):
    """Create a policy rule (platform_admin only)."""
    from agent_framework.services.policy.models import PolicyRule

    db_factory = request.app.state.session_factory
    async with db_factory() as db:
        rule = PolicyRule(
            tenant_id=user.tenant_id,
            role=body.role,
            action=body.action,
            resource_type=body.resource_type,
            effect=body.effect,
            conditions=body.conditions,
            priority=body.priority,
        )
        db.add(rule)
        await db.commit()
    return {"id": str(rule.id), "status": "created"}


@router.get("/rules")
async def list_rules(
    request: Request,
    user: AuthClaims = Depends(require_role("platform_admin", "tenant_admin")),
):
    """List policy rules for the caller's tenant."""
    from sqlalchemy import select
    from agent_framework.services.policy.models import PolicyRule

    db_factory = request.app.state.session_factory
    async with db_factory() as db:
        results = await db.execute(
            select(PolicyRule)
            .where(PolicyRule.tenant_id.in_([user.tenant_id, "default"]))
            .order_by(PolicyRule.priority.desc())
        )
        rules = results.scalars().all()
    return [
        {
            "id": str(r.id),
            "role": r.role,
            "action": r.action,
            "resource_type": r.resource_type,
            "effect": r.effect,
            "priority": r.priority,
        }
        for r in rules
    ]


@router.post("/grants", status_code=201)
async def grant_workspace_role(
    body: WorkspaceGrantCreate,
    request: Request,
    user: AuthClaims = Depends(
        require_role("platform_admin", "tenant_admin", "workspace_admin")
    ),
):
    """Grant a workspace-level role to a user."""
    from agent_framework.services.policy.models import WorkspaceGrant

    db_factory = request.app.state.session_factory
    async with db_factory() as db:
        grant = WorkspaceGrant(
            tenant_id=user.tenant_id,
            workspace_id=body.workspace_id,
            user_id=body.user_id,
            role=body.role,
            granted_by=user.sub,
        )
        db.add(grant)
        await db.commit()
    return {"id": str(grant.id), "status": "granted"}


@router.post("/seed")
async def seed_policies(
    request: Request,
    user: AuthClaims = Depends(require_role("platform_admin")),
):
    """Seed default policy rules from the permission matrix."""
    from agent_framework.services.policy.service import seed_default_policies

    db_factory = request.app.state.session_factory
    async with db_factory() as db:
        count = await seed_default_policies(db)
        await db.commit()
    return {"seeded": count}
