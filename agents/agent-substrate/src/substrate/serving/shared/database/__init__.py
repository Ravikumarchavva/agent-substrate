"""substrate.serving.shared.database — shared database connections."""

from __future__ import annotations

from substrate.serving.shared.database.dependency import get_db_session

__all__ = ["get_db_session"]
