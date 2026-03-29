"""Temporal activities — individual units of work executed by the worker."""

from __future__ import annotations

import logging
import time
from typing import Any

from temporalio import activity

logger = logging.getLogger(__name__)


@activity.defn
async def execute_adapter_step(step_input: dict[str, Any]) -> dict[str, Any]:
    """Execute a single adapter step within a pipeline.

    Args:
        step_input: Dict with keys: adapter_name, action, inputs, data_store_config
    """
    adapter_name = step_input["adapter_name"]
    action = step_input.get("action", "execute")
    inputs = step_input.get("inputs", {})

    logger.info("Executing adapter step: %s.%s", adapter_name, action)
    start = time.monotonic()

    # Import here to avoid circular deps at module level
    from raavan.catalog._temporal._activity_context import (
        get_catalog,
        get_data_store,
    )

    catalog = get_catalog()
    data_store = get_data_store()

    # Find the tool in the catalog
    entry = catalog.get(adapter_name)
    if entry is None or entry.tool is None:
        return {"error": f"Adapter '{adapter_name}' not found", "success": False}

    try:
        result = await entry.tool.run(**inputs)
        duration_ms = int((time.monotonic() - start) * 1000)

        # Store large results as DataRef
        output: dict[str, Any] = {
            "success": True,
            "content": result.content if hasattr(result, "content") else str(result),
            "duration_ms": duration_ms,
        }

        if (
            data_store
            and hasattr(result, "content")
            and len(str(result.content)) > 4096
        ):
            ref = await data_store.store(
                data=str(result.content).encode(),
                content_type="text/plain",
            )
            output["data_ref_id"] = str(ref.ref_id)
            output["content"] = f"[DataRef: {ref.ref_id}]"

        return output
    except Exception as exc:
        duration_ms = int((time.monotonic() - start) * 1000)
        logger.exception("Adapter step %s failed", adapter_name)
        return {"error": str(exc), "success": False, "duration_ms": duration_ms}


@activity.defn
async def execute_code_chain(chain_input: dict[str, Any]) -> dict[str, Any]:
    """Execute a code-based adapter chain via ChainRuntime.

    Args:
        chain_input: Dict with keys: code, timeout
    """
    code = chain_input["code"]
    timeout = chain_input.get("timeout", 120)

    logger.info("Executing code chain (timeout=%ds)", timeout)

    from raavan.catalog._temporal._activity_context import get_chain_runtime

    runtime = get_chain_runtime()
    if runtime is None:
        return {"error": "ChainRuntime not available", "success": False}

    try:
        result = await runtime.execute_script(code, timeout=timeout)
        return {
            "success": result.error is None,
            "outputs": [str(o) for o in result.outputs],
            "data_refs": [str(r.ref_id) for r in result.data_refs],
            "logs": result.logs,
            "error": result.error,
            "duration_ms": result.duration_ms,
        }
    except Exception as exc:
        logger.exception("Code chain execution failed")
        return {"error": str(exc), "success": False}
