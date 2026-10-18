"""Service base — shared patterns for all microservice FastAPI apps.

Every service inherits from this to get health checks, CORS,
OpenTelemetry, and standard error handling.
"""

from __future__ import annotations

from substrate.logger import setup_logging

import logging

from typing import Any, Callable, Optional

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from substrate_cloud.shared.auth.middleware import get_current_user
from substrate_cloud.shared.problem import install_problem_handlers

logger = logging.getLogger(__name__)

# Every router of every service is included with this: the same Keycloak token verification as the monolith.
# /health and /ready (below) are the only open endpoints.
AUTHENTICATED = [Depends(get_current_user)]


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
    setup_logging(
        service_name=title.lower().replace(" ", "-")
    )  # the application, not the library, configures logging
    app = FastAPI(title=title, version=version, lifespan=lifespan)
    install_problem_handlers(app)

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
