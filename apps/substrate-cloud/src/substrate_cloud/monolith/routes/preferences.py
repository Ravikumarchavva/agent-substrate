"""Per-user preferences, kept on the server so they follow the user to any browser or device.

Routes:
  GET /me/preferences  – the caller's preferences (empty values when never saved)
  PUT /me/preferences  – replace them

The chat route reads the same row, so the assistant's standing instructions and the user's timezone apply to every message however the
conversation is started, without the browser having to send them.
"""

from __future__ import annotations

from typing import Any, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from substrate_cloud.monolith.models import UserPreferences
from substrate_cloud.monolith.security.deps import AuthClaims, get_current_user
from substrate_cloud.monolith.security.rls_deps import get_tenant_scoped_db

router = APIRouter(prefix="/me/preferences", tags=["preferences"])

MAX_INSTRUCTIONS_CHARS = 2000
MAX_MODELS_BYTES = 4000
MAX_NAME_CHARS = 60


class PreferencesIO(BaseModel):
    custom_instructions: str = Field(default="", max_length=MAX_INSTRUCTIONS_CHARS)
    timezone: str = Field(default="", max_length=64)
    # What to call the user; empty means the name on their account.
    display_name: str = Field(default="", max_length=MAX_NAME_CHARS)
    models: dict[str, Any] = Field(default_factory=dict)

    @field_validator("timezone")
    @classmethod
    def _a_real_zone(cls, value: str) -> str:
        value = value.strip()
        if value:
            try:
                ZoneInfo(value)
            except (ZoneInfoNotFoundError, ValueError, OSError):
                raise ValueError(
                    "That is not a timezone name (for example Europe/London)."
                )
        return value

    @field_validator("display_name")
    @classmethod
    def _one_line(cls, value: str) -> str:
        return " ".join(value.split())

    @field_validator("models")
    @classmethod
    def _small(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(str(value)) > MAX_MODELS_BYTES:
            raise ValueError("Model preferences are too large.")
        return value


async def load_preferences(
    db: AsyncSession, user: AuthClaims
) -> Optional[UserPreferences]:
    row = await db.execute(
        select(UserPreferences).where(
            UserPreferences.tenant_id == (user.tenant_id or "default"),
            UserPreferences.user_identifier == user.sub,
        )
    )
    return row.scalars().first()


def instructions_for(prefs: Optional[UserPreferences]) -> str:
    """The standing instructions the assistant gets with every message: what to call the user, the timezone note, then the user's own words."""
    if prefs is None:
        return ""
    name = (
        f"The user likes to be called {prefs.display_name}. Use it when addressing them, naturally and not in every message."
        if prefs.display_name
        else ""
    )
    note = (
        f"User timezone: {prefs.timezone}. Always use this timezone when creating or interpreting calendar events and times."
        if prefs.timezone
        else ""
    )
    return "\n".join(
        filter(None, [name, note, (prefs.custom_instructions or "").strip()])
    )


def _out(prefs: Optional[UserPreferences]) -> PreferencesIO:
    if prefs is None:
        return PreferencesIO()
    return PreferencesIO(
        custom_instructions=prefs.custom_instructions or "",
        timezone=prefs.timezone or "",
        display_name=prefs.display_name or "",
        models=prefs.models or {},
    )


@router.get("", response_model=PreferencesIO)
async def get_preferences(
    db: AsyncSession = Depends(get_tenant_scoped_db),
    user: AuthClaims = Depends(get_current_user),
) -> PreferencesIO:
    return _out(await load_preferences(db, user))


@router.put("", response_model=PreferencesIO)
async def put_preferences(
    body: PreferencesIO,
    db: AsyncSession = Depends(get_tenant_scoped_db),
    user: AuthClaims = Depends(get_current_user),
) -> PreferencesIO:
    if not user.sub:
        raise HTTPException(status_code=401, detail="Not signed in")
    prefs = await load_preferences(db, user)
    if prefs is None:
        prefs = UserPreferences(
            tenant_id=user.tenant_id or "default", user_identifier=user.sub
        )
        db.add(prefs)
    prefs.custom_instructions = body.custom_instructions.strip() or None
    prefs.timezone = body.timezone or None
    prefs.display_name = body.display_name or None
    prefs.models = body.models
    await db.flush()
    return _out(prefs)
