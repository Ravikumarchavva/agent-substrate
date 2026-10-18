"""create_app: one FastAPI app over a container, serving the features its roles select."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from loamz.agents.bootstrap.container import Container
from loamz.agents.features import FEATURES, Feature
from loamz.agents.server import problems
from loamz.identity import PrimaryIssuer, TokenVerifier


def _verifier(container: Container) -> TokenVerifier | None:
    oidc = container.settings.oidc
    if not oidc.issuer:
        return None
    return TokenVerifier(
        PrimaryIssuer(issuer=oidc.issuer, audience=oidc.audience), insecure_http=oidc.insecure_http
    )


def create_app(
    container: Container,
    *,
    roles: set[str] | None = None,
    features: Sequence[Feature] = FEATURES,
) -> FastAPI:
    """`roles` defaults to the settings' roles; `{"all"}` mounts every feature. `features` is the catalog served."""
    wanted = roles or set(container.settings.roles)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await container.start(wanted)
        try:
            yield
        finally:
            await container.aclose()

    app = FastAPI(title="Loamz Agents", version="v1", lifespan=lifespan)
    app.state.container = container
    app.state.verifier = _verifier(container)
    problems.install(app)
    if container.settings.http.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=container.settings.http.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.get("/health", include_in_schema=False)
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    for feature in features:
        if "all" in wanted or feature.roles & wanted:
            app.include_router(feature.router())
    return app
