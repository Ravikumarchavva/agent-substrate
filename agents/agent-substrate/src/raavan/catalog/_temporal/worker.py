"""Temporal worker — registers workflows + activities and runs the task queue."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from temporalio.client import Client
from temporalio.worker import Worker

from raavan.catalog._temporal.activities import (
    execute_adapter_step,
    execute_code_chain,
)
from raavan.catalog._temporal.client import TASK_QUEUE
from raavan.catalog._temporal.workflows import (
    ChainWorkflow,
    PipelineWorkflow,
)

if TYPE_CHECKING:
    from raavan.catalog._chain_runtime import ChainRuntime
    from raavan.catalog._data_ref import DataRefStore
    from raavan.core.tools.catalog import CapabilityRegistry

logger = logging.getLogger(__name__)


async def run_worker(
    client: Client,
    catalog: "CapabilityRegistry",
    data_store: "DataRefStore | None" = None,
    chain_runtime: "ChainRuntime | None" = None,
) -> None:
    """Start the Temporal worker — blocks until cancelled.

    Call this from the server lifespan as a background task:
        asyncio.create_task(run_worker(client, catalog, data_store, chain_runtime))
    """
    # Inject shared state into activity context
    from raavan.catalog._temporal._activity_context import (
        init_activity_context,
    )

    init_activity_context(catalog, data_store, chain_runtime)

    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[PipelineWorkflow, ChainWorkflow],
        activities=[execute_adapter_step, execute_code_chain],
    )

    logger.info("Temporal worker started on queue '%s'", TASK_QUEUE)
    await worker.run()


async def main() -> None:
    """Standalone worker entry point for development/debugging."""
    logging.basicConfig(level=logging.INFO)
    client = await Client.connect("localhost:7233")

    # In standalone mode we need a minimal catalog — import here to avoid
    # heavy deps when used as a library
    from raavan.core.tools.catalog import CapabilityRegistry

    catalog = CapabilityRegistry()
    await run_worker(client, catalog)


if __name__ == "__main__":
    asyncio.run(main())
