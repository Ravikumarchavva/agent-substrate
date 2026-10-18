"""substrate_cloud.shared.database — shared database connections."""

from __future__ import annotations

from substrate_cloud.shared.database.dependency import get_db_session

__all__ = ["get_db_session"]
