from __future__ import annotations

from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator

MAX_INSTRUCTIONS_CHARS = 2000
MAX_MODELS_CHARS = 4000
MAX_NAME_CHARS = 60


class Preferences(BaseModel):
    """What a user has chosen: standing instructions, timezone, what to call them, and the UI's model choices."""

    custom_instructions: str = Field(default="", max_length=MAX_INSTRUCTIONS_CHARS)
    timezone: str = Field(default="", max_length=64)
    display_name: str = Field(default="", max_length=MAX_NAME_CHARS)
    models: dict[str, Any] = Field(default_factory=dict)

    @field_validator("custom_instructions")
    @classmethod
    def _trimmed(cls, value: str) -> str:
        return value.strip()

    @field_validator("timezone")
    @classmethod
    def _a_real_zone(cls, value: str) -> str:
        value = value.strip()
        if value:
            try:
                ZoneInfo(value)
            except ZoneInfoNotFoundError, ValueError, OSError:
                raise ValueError(
                    "That is not a timezone name (for example Europe/London)."
                ) from None
        return value

    @field_validator("display_name")
    @classmethod
    def _one_line(cls, value: str) -> str:
        return " ".join(value.split())

    @field_validator("models")
    @classmethod
    def _small(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(str(value)) > MAX_MODELS_CHARS:
            raise ValueError("Model preferences are too large.")
        return value

    def instructions(self) -> str:
        """The standing instructions the assistant gets with every message."""
        name = (
            f"The user likes to be called {self.display_name}. Use it when addressing them, naturally and "
            "not in every message."
            if self.display_name
            else ""
        )
        zone = (
            f"User timezone: {self.timezone}. Always use this timezone when creating or interpreting calendar "
            "events and times."
            if self.timezone
            else ""
        )
        return "\n".join(filter(None, [name, zone, self.custom_instructions]))
