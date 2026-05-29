"""FastAPI routes for the Document Intelligence Pipeline demo."""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import AsyncIterator

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import HTMLResponse, Response, StreamingResponse

from bus import bus
from pipeline import run_pipeline
from tools import catalog, EMBEDDINGS_DIR
from ravi.logger import setup_logging

logger = setup_logging(mode="pretty", handler="console")

BASE_DIR = Path(__file__).parent
HTML_FILE = BASE_DIR / "index.html"
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

router = APIRouter()


@router.get("/favicon.ico", include_in_schema=False)
async def favicon() -> Response:
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
        '<rect width="32" height="32" rx="6" fill="#6366f1"/>'
        '<text x="16" y="23" text-anchor="middle" font-size="20" '
        'font-family="monospace" fill="white">r</text></svg>'
    )
    return Response(content=svg, media_type="image/svg+xml")


@router.get("/", response_class=HTMLResponse)
async def serve_ui() -> HTMLResponse:
    return HTMLResponse(HTML_FILE.read_text())


@router.post("/upload")
async def upload_file(file: UploadFile = File(...)) -> dict:
    data = await file.read()
    job_id = uuid.uuid4().hex[:12]
    dest = UPLOAD_DIR / f"{job_id}_{file.filename}"
    dest.write_bytes(data)
    logger.info(
        "Upload received job=%s filename=%s size=%d", job_id, file.filename, len(data)
    )
    asyncio.create_task(
        run_pipeline(job_id, dest, file.filename or "unknown", len(data))
    )
    return {"job_id": job_id, "filename": file.filename, "size": len(data)}


@router.get("/stream/{job_id}")
async def stream_events(job_id: str) -> StreamingResponse:
    async def generate() -> AsyncIterator[str]:
        q = bus.sse_queue(job_id)
        while True:
            try:
                item = await asyncio.wait_for(q.get(), timeout=120.0)
            except asyncio.TimeoutError:
                yield "data: {}\n\n"
                continue
            except Exception:
                logger.exception("SSE generator error job=%s", job_id)
                break
            if item is None:
                yield "data: [DONE]\n\n"
                break
            yield f"data: {json.dumps(item)}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/embeddings")
async def list_embeddings() -> list[dict]:
    result = []
    for f in sorted(EMBEDDINGS_DIR.glob("*.json")):
        try:
            rec = json.loads(f.read_text())
            result.append(
                {
                    "job_id": rec.get("job_id"),
                    "filename": rec.get("filename"),
                    "method": rec.get("method"),
                    "dimensions": rec.get("dimensions"),
                    "created_at": rec.get("created_at"),
                    "preview": rec.get("embedding", [])[:4],
                }
            )
        except Exception:
            logger.exception("Failed to parse embedding file %s", f.name)
    return result


@router.get("/catalog")
async def list_catalog() -> dict:
    tools = [t.name for t in catalog.all_tools()]
    return {"tools": tools, "total": len(tools)}
