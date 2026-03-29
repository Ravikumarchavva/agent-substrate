"""Shared DB session dependency for microservices.

Usage in any service ``routes.py``::

    from raavan.shared.database.dependency import get_db_session
    from fastapi import Depends
    from sqlalchemy.ext.asyncio import AsyncSession

    @router.get("/items")
    async def list_items(db: AsyncSession = Depends(get_db_session)):
        ...

Requires ``app.state.session_factory`` to be wired in the service lifespan.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession


async def get_db_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Yield an async DB session with commit/rollback lifecycle."""
    async with request.app.state.session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
