"""PipelineStore — persist pipeline definitions in Postgres.

Uses raw SQL via the injected async session factory — no ORM model import
required, keeping capabilities independent of the serving layer.
"""

from __future__ import annotations

import json
from typing import Any

from substrate.capabilities.pipeline.engine import PipelineDef
from substrate.logger import setup_logging

logger = setup_logging()


class PipelineStore:
    """Persist pipeline definitions in the ``adapter_pipelines`` Postgres table."""

    _TABLE = "adapter_pipelines"

    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    async def save(self, pipeline: PipelineDef) -> None:
        """Save or update a pipeline definition (upsert by name)."""
        from sqlalchemy import text

        definition = json.dumps(pipeline.to_dict())
        async with self._session_factory() as session:
            await session.execute(
                text(
                    f"INSERT INTO {self._TABLE} (name, description, definition_json, created_by) "  # noqa: S608
                    "VALUES (:name, :description, :definition, :created_by) "
                    "ON CONFLICT (name) DO UPDATE SET "
                    "  definition_json = EXCLUDED.definition_json, "
                    "  description = EXCLUDED.description"
                ),
                {
                    "name": pipeline.name,
                    "description": pipeline.description,
                    "definition": definition,
                    "created_by": pipeline.created_by,
                },
            )
            await session.commit()

    async def load(self, name: str) -> PipelineDef | None:
        """Load a pipeline by name."""
        from sqlalchemy import text

        async with self._session_factory() as session:
            result = await session.execute(
                text(f"SELECT definition_json FROM {self._TABLE} WHERE name = :name"),  # noqa: S608
                {"name": name},
            )
            row = result.first()
            if row is None:
                return None
            return PipelineDef.from_dict(json.loads(row.definition_json))

    async def list_all(self) -> list[PipelineDef]:
        """List all saved pipelines ordered by creation time."""
        from sqlalchemy import text

        async with self._session_factory() as session:
            result = await session.execute(
                text(f"SELECT definition_json FROM {self._TABLE} ORDER BY created_at"),  # noqa: S608
            )
            return [
                PipelineDef.from_dict(json.loads(row.definition_json)) for row in result
            ]

    async def delete(self, name: str) -> bool:
        """Delete a pipeline by name. Returns True if a row was deleted."""
        from sqlalchemy import text

        async with self._session_factory() as session:
            result = await session.execute(
                text(f"DELETE FROM {self._TABLE} WHERE name = :name"),  # noqa: S608
                {"name": name},
            )
            await session.commit()
            return result.rowcount > 0  # type: ignore[union-attr]
