"""Service base — shared patterns for all microservice FastAPI apps.

Every service inherits from this to get health checks, CORS,
OpenTelemetry, and standard error handling.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Callable, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

logger = logging.getLogger(__name__)


def create_service_app(
    *,
    title: str,
    version: str = "0.1.0",
    lifespan: Optional[Callable] = None,
    cors_origins: list[str] | None = None,
    enable_otel: bool = True,
) -> FastAPI:
    """Factory for a standard microservice FastAPI app.

    Adds:
    - CORS middleware
    - Health and readiness endpoints
    - OpenTelemetry instrumentation (when available)
    """
    app = FastAPI(title=title, version=version, lifespan=lifespan)

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins or ["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Health
    @app.get("/health", tags=["infra"])
    async def health():
        return {"status": "ok", "service": title}

    @app.get("/ready", tags=["infra"])
    async def readiness():
        return {"status": "ready", "service": title}

    # OpenTelemetry
    if enable_otel:
        try:
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
            FastAPIInstrumentor.instrument_app(app)
        except ImportError:
            logger.debug("OpenTelemetry not available, skipping instrumentation")

    return app


async def init_service_db(
    database_url: str,
    base: Any,
    echo: bool = False,
) -> tuple:
    """Initialize database for a service. Returns (engine, session_factory)."""
    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )

    engine = create_async_engine(
        database_url,
        echo=echo,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
    )

    session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with engine.begin() as conn:
        await conn.run_sync(base.metadata.create_all)

    return engine, session_factory
