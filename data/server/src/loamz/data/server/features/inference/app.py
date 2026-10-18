"""create_inference_app: the `inference` worker role as its own process."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from loamz.data.server.features.inference.api import router
from loamz.data.server.features.inference.service import inference
from loamz.data.server.kernel import problems
from loamz.data.server.kernel.container import Container


def create_inference_app(container: Container) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await container.get(inference).start()
        try:
            yield
        finally:
            await container.aclose()

    app = FastAPI(title="Loamz Data inference", version="1.0.0", lifespan=lifespan)
    app.state.container = container
    problems.install(app, container.settings)
    app.include_router(router)
    return app
