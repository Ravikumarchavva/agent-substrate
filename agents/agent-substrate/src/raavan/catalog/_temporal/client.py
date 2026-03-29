"""Temporal client wrapper for durable workflow orchestration."""

from __future__ import annotations

import logging
from typing import Any

from temporalio.client import Client

logger = logging.getLogger(__name__)

TASK_QUEUE = "agent-workflows"
NAMESPACE = "default"


class TemporalClient:
    """Thin wrapper around the Temporal SDK client."""

    def __init__(
        self, host: str = "localhost:7233", namespace: str = NAMESPACE
    ) -> None:
        self._host = host
        self._namespace = namespace
        self._client: Client | None = None

    async def connect(self) -> None:
        self._client = await Client.connect(self._host, namespace=self._namespace)
        logger.info("Connected to Temporal at %s (ns=%s)", self._host, self._namespace)

    @property
    def client(self) -> Client:
        if self._client is None:
            raise RuntimeError("TemporalClient not connected — call connect() first")
        return self._client

    async def start_pipeline_workflow(
        self,
        pipeline_name: str,
        definition: dict[str, Any],
        *,
        workflow_id: str | None = None,
    ) -> str:
        """Start a PipelineWorkflow and return the workflow ID."""
        from raavan.catalog._temporal.workflows import PipelineWorkflow

        wf_id = workflow_id or f"pipeline-{pipeline_name}-{_short_id()}"
        await self.client.start_workflow(
            PipelineWorkflow.run,
            definition,
            id=wf_id,
            task_queue=TASK_QUEUE,
        )
        logger.info("Started PipelineWorkflow %s", wf_id)
        return wf_id

    async def start_chain_workflow(
        self,
        code: str,
        description: str,
        *,
        timeout: int = 120,
        workflow_id: str | None = None,
    ) -> str:
        """Start a ChainWorkflow and return the workflow ID."""
        from raavan.catalog._temporal.workflows import ChainWorkflow

        wf_id = workflow_id or f"chain-{_short_id()}"
        await self.client.start_workflow(
            ChainWorkflow.run,
            {"code": code, "description": description, "timeout": timeout},
            id=wf_id,
            task_queue=TASK_QUEUE,
        )
        logger.info("Started ChainWorkflow %s", wf_id)
        return wf_id

    async def query_workflow(self, workflow_id: str) -> dict[str, Any]:
        """Query a running workflow for its current status."""
        handle = self.client.get_workflow_handle(workflow_id)
        desc = await handle.describe()
        return {
            "workflow_id": workflow_id,
            "status": desc.status.name if desc.status else "UNKNOWN",
            "start_time": str(desc.start_time) if desc.start_time else None,
            "close_time": str(desc.close_time) if desc.close_time else None,
        }

    async def cancel_workflow(self, workflow_id: str) -> None:
        """Cancel a running workflow."""
        handle = self.client.get_workflow_handle(workflow_id)
        await handle.cancel()
        logger.info("Cancelled workflow %s", workflow_id)

    async def get_result(self, workflow_id: str) -> Any:
        """Wait for and return the workflow result."""
        handle = self.client.get_workflow_handle(workflow_id)
        return await handle.result()

    async def signal_workflow(
        self, workflow_id: str, signal_name: str, payload: Any = None
    ) -> None:
        """Send a signal to a running workflow."""
        handle = self.client.get_workflow_handle(workflow_id)
        await handle.signal(signal_name, payload)
        logger.info("Sent signal '%s' to workflow %s", signal_name, workflow_id)


def _short_id() -> str:
    import uuid

    return uuid.uuid4().hex[:8]
