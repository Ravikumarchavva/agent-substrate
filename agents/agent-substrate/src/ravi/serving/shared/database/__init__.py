"""ravi.serving.shared.database — shared database connections."""

from __future__ import annotations

from ravi.serving.shared.database.dependency import get_db_session

__all__ = ["get_db_session"]
