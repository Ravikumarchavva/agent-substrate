"""Identity service: HTTP routes. Sign-in and tokens are Keycloak's; this keeps profiles.

GET /users/me               The caller's profile (created or refreshed from the verified token)
GET /users/{subject}        A user of the caller's tenant (tenant or platform administrators)
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from substrate_identity import Forbidden

from substrate_cloud.services.identity.service import find_profile, upsert_profile
from substrate_cloud.shared.auth.claims import AuthClaims
from substrate_cloud.shared.auth.middleware import get_current_user
from substrate_cloud.shared.contracts.auth import UserOut
from substrate_cloud.shared.problem import Problem

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserOut)
async def get_me(request: Request, user: AuthClaims = Depends(get_current_user)):
    if not user.tenant_id:
        raise Forbidden("A service account has no profile")
    name = request.state.identity.name
    async with request.app.state.session_factory() as db:
        profile = await upsert_profile(db, user, display_name=name)
        await db.commit()
        await db.refresh(profile)
        return profile


@router.get("/{subject}", response_model=UserOut)
async def get_user(
    subject: str, request: Request, user: AuthClaims = Depends(get_current_user)
):
    if not user.is_admin:
        raise Forbidden("Only administrators look up other users")
    async with request.app.state.session_factory() as db:
        profile = await find_profile(db, user.tenant_id, subject)
    if profile is None:
        raise Problem(404, "not-found", "User was not found in this tenant")
    return profile
