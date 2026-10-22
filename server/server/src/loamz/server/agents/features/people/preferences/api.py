from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from loamz.server.agents.bootstrap.http import CallerDep, use
from loamz.server.agents.features.people.preferences.model import Preferences
from loamz.server.agents.features.people.preferences.service import PreferencesService, preferences

Service = Annotated[PreferencesService, Depends(use(preferences))]

router = APIRouter(prefix="/api/v1/me/preferences", tags=["preferences"])


@router.get("", response_model=Preferences)
async def get_preferences(caller: CallerDep, service: Service) -> Preferences:
    return await service.get(caller)


@router.put("", response_model=Preferences)
async def replace_preferences(
    body: Preferences, caller: CallerDep, service: Service
) -> Preferences:
    return await service.replace(caller, body)
