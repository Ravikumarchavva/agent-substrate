"""Shared API contract for the Identity service's user profiles.

Sign-in, tokens and refresh belong to Keycloak (ADR 0007); this service only keeps a profile mirror of the people who have used
the cell, keyed by their Keycloak subject.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class UserOut(BaseModel):
    id: uuid.UUID
    subject: str
    email: str = ""
    display_name: str = ""
    role: str = "end_user"
    tenant_id: str
    created_at: datetime
    model_config = {"from_attributes": True}
