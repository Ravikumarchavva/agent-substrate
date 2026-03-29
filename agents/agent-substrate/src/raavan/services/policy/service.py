"""Policy Authorization Service — business logic.

Evaluates policy decisions based on role→action→resource rules.
Every mutating endpoint in the platform calls this before executing side effects.
"""

from __future__ import annotations

import logging

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from raavan.services.policy.models import PolicyRule, WorkspaceGrant
from raavan.shared.auth.claims import AuthClaims

logger = logging.getLogger(__name__)

# Default permission matrix (per docs/microservices/02-role-and-responsibility-matrix.md)
DEFAULT_PERMISSIONS: dict[str, set[str]] = {
    "platform_admin": {
        "manage_global_policies",
        "manage_tenant_users",
        "manage_workspace_roles",
        "submit_conversation",
        "approve_hitl_request",
        "cancel_or_retry_workflow",
        "deploy_or_update_service",
        "read_audit_reports",
    },
    "tenant_admin": {
        "manage_tenant_users",
        "manage_workspace_roles",
        "submit_conversation",
        "approve_hitl_request",
        "cancel_or_retry_workflow",
        "read_audit_reports",
    },
    "workspace_admin": {
        "manage_workspace_roles",
        "submit_conversation",
        "approve_hitl_request",
        "cancel_or_retry_workflow",
        "read_audit_reports",
    },
    "operator": {
        "submit_conversation",
        "approve_hitl_request",
        "cancel_or_retry_workflow",
        "read_audit_reports",
    },
    "developer": {
        "submit_conversation",
        "deploy_or_update_service",
        "read_audit_reports",
    },
    "analyst": {
        "read_audit_reports",
    },
    "end_user": {
        "submit_conversation",
        "approve_hitl_request",
    },
    "service_runtime": {
        "cancel_or_retry_workflow",
        "execute_tool_call",
    },
}


async def check_permission(
    db: AsyncSession,
    claims: AuthClaims,
    action: str,
    resource_type: str = "*",
    resource_id: str = "*",
) -> bool:
    """Evaluate whether the caller is authorized for the requested action.

    Two-phase evaluation:
    1. Check DB policy rules (overrides)
    2. Fall back to default permission matrix
    """
    # Phase 1: DB rules (explicit deny wins)
    rules = await db.execute(
        select(PolicyRule)
        .where(
            and_(
                PolicyRule.is_active.is_(True),
                PolicyRule.tenant_id.in_([claims.tenant_id, "*"]),
                PolicyRule.role == claims.role,
                PolicyRule.action == action,
                PolicyRule.resource_type.in_([resource_type, "*"]),
            )
        )
        .order_by(PolicyRule.priority.desc())
    )
    for rule in rules.scalars():
        if rule.effect == "deny":
            logger.debug(
                "Policy DENY: %s cannot %s on %s (rule %s)",
                claims.sub,
                action,
                resource_type,
                rule.id,
            )
            return False
        if rule.effect == "allow":
            return True

    # Phase 2: Default matrix
    allowed = DEFAULT_PERMISSIONS.get(claims.role, set())
    return action in allowed


async def get_effective_role(
    db: AsyncSession,
    user_id: str,
    tenant_id: str,
    workspace_id: str,
) -> str:
    """Determine the effective role for a user in a workspace.

    Checks workspace grants first, falls back to identity-level role.
    """
    result = await db.execute(
        select(WorkspaceGrant).where(
            and_(
                WorkspaceGrant.user_id == user_id,
                WorkspaceGrant.tenant_id == tenant_id,
                WorkspaceGrant.workspace_id == workspace_id,
                WorkspaceGrant.is_active.is_(True),
            )
        )
    )
    grant = result.scalar_one_or_none()
    if grant:
        return grant.role
    return "end_user"


async def seed_default_policies(db: AsyncSession) -> int:
    """Seed the default policy rules from the permission matrix."""
    count = 0
    for role, actions in DEFAULT_PERMISSIONS.items():
        for action in actions:
            existing = await db.execute(
                select(PolicyRule).where(
                    and_(
                        PolicyRule.role == role,
                        PolicyRule.action == action,
                        PolicyRule.tenant_id == "default",
                    )
                )
            )
            if existing.scalar_one_or_none():
                continue
            db.add(
                PolicyRule(
                    tenant_id="default",
                    role=role,
                    action=action,
                    resource_type="*",
                    effect="allow",
                )
            )
            count += 1
    await db.flush()
    return count
