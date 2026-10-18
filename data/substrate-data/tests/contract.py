"""Builds the OpenAPI schema without storage, identity or compute (routes only)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from pydantic import SecretStr

from substrate_data.api.app import create_app
from substrate_data.core.models import Settings


def build_schema() -> dict[str, Any]:
    settings = Settings(_env_file=None, controller_token=SecretStr("x" * 48))
    app = create_app(
        settings, SimpleNamespace(settings=settings)
    )  # routes only; nothing is started
    return app.openapi()


SCHEMA_FILE = Path(__file__).resolve().parent.parent / "docs" / "openapi.json"
