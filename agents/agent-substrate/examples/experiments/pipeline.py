"""Pipeline orchestrator — coordinates the full agent pipeline with pub/sub fan-out."""

from __future__ import annotations

import asyncio
from pathlib import Path

from bus import bus
from agents import proxy_agent, ocr_agent, pii_agent, classifier_agent, embedding_agent
from tools import catalog
from ravi.logger import setup_logging

logger = setup_logging(mode="pretty", handler="console")


async def run_pipeline(job_id: str, file_path: Path, filename: str, size: int) -> None:
    # Subscribe BEFORE the pipeline starts producing events
    text_ready_q = bus.subscribe(job_id, "text:ready")

    try:
        logger.info("Pipeline started job=%s filename=%s", job_id, filename)
        await bus.emit(
            job_id,
            "Pipeline",
            "pipeline:start",
            {
                "message": "Pipeline started",
                "filename": filename,
            },
        )

        # Stage 1: ProxyAgent (validate)
        ok = await proxy_agent(job_id, file_path, filename, size)
        if not ok:
            await bus.emit(
                job_id,
                "Pipeline",
                "pipeline:error",
                {
                    "message": "Pipeline aborted — file rejected by ProxyAgent",
                },
            )
            return

        # Stage 2: OCRAgent (extract text)
        text = await ocr_agent(job_id, file_path, filename)
        if not text:
            logger.warning("Pipeline aborted — OCR produced no text job=%s", job_id)
            await bus.emit(
                job_id,
                "Pipeline",
                "pipeline:error",
                {
                    "message": "OCR produced no text",
                },
            )
            return

        payload = await asyncio.wait_for(text_ready_q.get(), timeout=60.0)
        extracted_text = payload["text"]

        # Stage 3: Fan-out to 3 parallel subscribers
        await bus.emit(
            job_id,
            "Pipeline",
            "pipeline:fanout",
            {
                "message": "Dispatching to 3 agents in parallel…",
            },
        )
        await asyncio.gather(
            pii_agent(job_id, extracted_text, filename),
            classifier_agent(job_id, extracted_text, filename),
            embedding_agent(job_id, extracted_text, filename),
        )

        logger.info("Pipeline completed job=%s", job_id)
        await bus.emit(
            job_id,
            "Pipeline",
            "pipeline:done",
            {
                "message": "All agents completed ✓",
                "tools_used": [t.name for t in catalog.all_tools()],
            },
        )

    except asyncio.TimeoutError:
        logger.warning("Pipeline timed out waiting for OCR result job=%s", job_id)
        await bus.emit(
            job_id,
            "Pipeline",
            "pipeline:error",
            {
                "message": "Pipeline timed out waiting for OCR result",
            },
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Unexpected pipeline error job=%s", job_id)
        await bus.emit(
            job_id,
            "Pipeline",
            "pipeline:error",
            {
                "message": "Unexpected error — check server logs",
            },
        )
    finally:
        await bus.done(job_id)
        await asyncio.sleep(2)
        bus.cleanup(job_id)
