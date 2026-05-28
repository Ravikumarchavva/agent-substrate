"""Agent pipeline functions — each runs a tool via the catalog and emits events."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Optional

from bus import bus
from tools import catalog
from ravi.logger import setup_logging

logger = setup_logging(mode='pretty', handler='console')


async def proxy_agent(job_id: str, file_path: Path, filename: str, size: int) -> bool:
    await bus.emit(job_id, "ProxyAgent", "agent:start", {
        "message": f"Validating {filename}…",
    })
    await asyncio.sleep(0.2)

    tool_ = catalog.get_tool("validate_file")
    result = await tool_.execute(
        file_path=str(file_path), filename=filename, size_bytes=size
    )
    meta = result.app_data or {}

    if not meta.get("valid", False):
        msg = result.content[0].text if result.content else "Validation failed"
        logger.warning("ProxyAgent rejected file job=%s filename=%s reason=%s", job_id, filename, msg)
        await bus.emit(job_id, "ProxyAgent", "agent:error", {
            "message": msg,
            "size_mb": meta.get("size_mb", 0),
        })
        return False

    await bus.emit(job_id, "ProxyAgent", "agent:done", {
        "message": f"✓ Validated — {meta['size_mb']:.2f} MB",
        "size_mb": meta["size_mb"],
        "ext": meta.get("ext", ""),
    })
    await bus.emit(job_id, "ProxyAgent", "file:ready", {
        "file_path": str(file_path),
        "filename": filename,
    })
    return True


async def ocr_agent(job_id: str, file_path: Path, filename: str) -> Optional[str]:
    await bus.emit(job_id, "OCRAgent", "agent:start", {
        "message": f"Extracting text from {filename}…",
    })

    tool_ = catalog.get_tool("extract_text")
    result = await tool_.execute(file_path=str(file_path), filename=filename)
    meta = result.app_data or {}
    text = result.content[0].text if result.content else ""

    logger.info("OCRAgent extracted job=%s words=%d", job_id, meta.get("word_count", 0))
    await bus.emit(job_id, "OCRAgent", "agent:done", {
        "message": f"✓ Extracted {meta.get('word_count', 0):,} words",
        "word_count": meta.get("word_count", 0),
        "char_count": meta.get("char_count", 0),
        "preview": text[:400] + ("…" if len(text) > 400 else ""),
        "full_text": text,
    })
    await bus.emit(job_id, "OCRAgent", "text:ready", {
        "text": text,
        "filename": filename,
    })
    return text


async def pii_agent(job_id: str, text: str, filename: str) -> None:
    await bus.emit(job_id, "PIIAgent", "agent:start", {"message": "Scanning for PII…"})
    await asyncio.sleep(0.15)

    tool_ = catalog.get_tool("detect_pii")
    result = await tool_.execute(text=text)
    meta = result.app_data or {}

    if meta.get("risk_level") == "high":
        logger.warning("PIIAgent detected PII job=%s findings=%s", job_id, meta.get("findings"))

    await bus.emit(job_id, "PIIAgent", "agent:done", {
        "message": result.content[0].text if result.content else "",
        "findings": meta.get("findings", {}),
        "risk_level": meta.get("risk_level", "unknown"),
        "clean": meta.get("clean", True),
    })


async def classifier_agent(job_id: str, text: str, filename: str) -> None:
    await bus.emit(job_id, "ClassifierAgent", "agent:start", {
        "message": "Classifying document…",
    })
    await asyncio.sleep(0.1)

    tool_ = catalog.get_tool("classify_document")
    result = await tool_.execute(text=text, filename=filename)
    meta = result.app_data or {}

    await bus.emit(job_id, "ClassifierAgent", "agent:done", {
        "message": result.content[0].text if result.content else "",
        "type": meta.get("type", "unknown"),
        "confidence": meta.get("confidence", 0),
        "scores": meta.get("scores", {}),
    })


async def embedding_agent(job_id: str, text: str, filename: str) -> None:
    await bus.emit(job_id, "EmbeddingAgent", "agent:start", {
        "message": "Generating embedding…",
    })

    tool_ = catalog.get_tool("generate_embedding")
    result = await tool_.execute(text=text, job_id=job_id, filename=filename)
    meta = result.app_data or {}

    await bus.emit(job_id, "EmbeddingAgent", "agent:done", {
        "message": result.content[0].text if result.content else "",
        "method": meta.get("method", ""),
        "dimensions": meta.get("dimensions", 0),
        "file": meta.get("file", ""),
        "preview": meta.get("preview", []),
    })
