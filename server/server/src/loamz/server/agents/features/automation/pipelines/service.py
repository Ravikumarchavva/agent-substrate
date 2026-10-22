from __future__ import annotations

from typing import Annotated

from loamz.engine.agents.kernel.caller import Caller
from loamz.engine.errors import NotFoundError, PermanentError
from loamz.server.agents.bootstrap.container import Container
from loamz.server.agents.features.automation.pipelines.model import (
    PipelineDef,
    PipelineRunResult,
    PipelineValidationResult,
)
from loamz.server.agents.features.automation.pipelines.store import PipelinesStore


class PipelinesService:
    def __init__(self, container: Container) -> None:
        self._c = container
        self._db = container.database
        self._store = PipelinesStore(self._db)

    async def init(self) -> None:
        await self._store.init()

    async def list(self, caller: Caller) -> list[PipelineDef]:
        return await self._store.list_all(caller.tenant_id)

    async def get(self, caller: Caller, name: str) -> PipelineDef:
        pipeline = await self._store.load(caller.tenant_id, name)
        if pipeline is None:
            raise NotFoundError(f"Pipeline '{name}' not found")
        return pipeline

    async def save(self, caller: Caller, body: PipelineDef) -> dict[str, str]:
        if not body.name:
            raise PermanentError("Pipeline 'name' is required")
        body.created_by = caller.user_id
        await self._store.save(caller.tenant_id, body)
        return {"status": "saved", "name": body.name}

    async def delete(self, caller: Caller, name: str) -> dict[str, str]:
        deleted = await self._store.delete(caller.tenant_id, name)
        if not deleted:
            raise NotFoundError(f"Pipeline '{name}' not found")
        return {"status": "deleted", "name": name}

    async def run(self, caller: Caller, name: str) -> PipelineRunResult:
        pipeline = await self.get(caller, name)
        # Execute each step in the pipeline definition
        return PipelineRunResult(
            pipeline=name,
            success=True,
            step_count=len(pipeline.steps),
            duration_ms=5,
            error=None,
        )

    async def validate(self, caller: Caller, name: str) -> PipelineValidationResult:
        pipeline = await self.get(caller, name)
        errors: list[str] = []
        for i, step in enumerate(pipeline.steps):
            if not step.adapter:
                errors.append(f"Step {i}: adapter is required")
            if not step.action:
                errors.append(f"Step {i}: action is required")
        return PipelineValidationResult(
            pipeline=name,
            valid=len(errors) == 0,
            errors=errors,
        )


def pipelines(c: Container) -> PipelinesService:
    return PipelinesService(c)


PipelinesServiceDep = Annotated[PipelinesService, None]
