"""BaseTool subclasses for each agent capability + AgentCatalog registration."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mineru_vl_utils import MinerUClient

# Set by server.py lifespan once the vLLM engine is loaded.
# When set, ExtractTextTool uses the warm async engine instead of spawning a process.
_mineru_client: MinerUClient | None = None


def _to_md(extract_result: list) -> str:
    """Convert ExtractResult (list[ContentBlock]) to markdown via MinerU's own converter."""
    from mineru_vl_utils.post_process.json2markdown import json2md
    return json2md(extract_result)

from ravi.kernel.tools.base_tool import BaseTool, ToolResult, ToolRisk
from ravi.kernel.agent_catalog import AgentCatalog
from ravi.kernel.messages.content import TextBlock
from ravi.extensions.guardrails.pii import _PII_PATTERNS
from ravi.logger import setup_logging

logger = setup_logging(mode='pretty', handler='console')

BASE_DIR = Path(__file__).parent
EMBEDDINGS_DIR = BASE_DIR / "embeddings"
MAX_SIZE_MB = 20


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


class ValidateFileTool(BaseTool):
    """ProxyAgent tool: validate that a file meets the size constraint."""

    def __init__(self) -> None:
        super().__init__(
            name="validate_file",
            description="Validate uploaded file: check size limit and file type",
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "filename": {"type": "string"},
                    "size_bytes": {"type": "integer"},
                    "max_mb": {"type": "integer"},
                },
                "required": ["file_path", "filename", "size_bytes"],
            },
            risk=ToolRisk.SAFE,
        )

    async def execute(  # type: ignore[override]
        self,
        file_path: str,
        filename: str,
        size_bytes: int,
        max_mb: int = MAX_SIZE_MB,
    ) -> ToolResult:
        size_mb = size_bytes / (1024 * 1024)
        if size_mb > max_mb:
            return ToolResult(
                content=[TextBlock(text=f"REJECTED: {size_mb:.2f} MB exceeds {max_mb} MB limit")],
                app_data={"valid": False, "size_mb": round(size_mb, 3)},
            )
        ext = Path(filename).suffix.lower()
        supported = {".txt", ".md", ".pdf", ".png", ".jpg", ".jpeg", ".csv", ".json"}
        if ext not in supported:
            return ToolResult(
                content=[TextBlock(text=f"REJECTED: unsupported format '{ext}'")],
                app_data={"valid": False, "size_mb": round(size_mb, 3)},
            )
        return ToolResult(
            content=[TextBlock(text=f"VALID: {filename} ({size_mb:.2f} MB)")],
            app_data={"valid": True, "size_mb": round(size_mb, 3), "ext": ext},
        )


class ExtractTextTool(BaseTool):
    """OCRAgent tool: extract text from any supported document format."""

    def __init__(self) -> None:
        super().__init__(
            name="extract_text",
            description="Extract text from PDF, image, or text file",
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "filename": {"type": "string"},
                },
                "required": ["file_path", "filename"],
            },
            risk=ToolRisk.SAFE,
        )

    async def execute(  # type: ignore[override]
        self, file_path: str, filename: str
    ) -> ToolResult:
        ext = Path(filename).suffix.lower()
        if ext in (".txt", ".md", ".csv", ".json"):
            text = Path(file_path).read_text(errors="replace").strip()
        elif _mineru_client is not None:
            text = await self._mineru_vl(Path(file_path), ext)
        elif ext in (".pdf", ".png", ".jpg", ".jpeg"):
            text = await asyncio.to_thread(self._mineru_cli, Path(file_path))
        else:
            text = f"[Unsupported format: {ext}]"
        words = len(text.split())
        return ToolResult(
            content=[TextBlock(text=text)],
            app_data={"word_count": words, "char_count": len(text)},
        )

    @staticmethod
    async def _mineru_vl(path: Path, ext: str) -> str:
        """Extract via the warm vLLM async engine — no process spawn, model stays loaded."""
        import io
        import aiofiles
        from PIL import Image

        if ext in (".png", ".jpg", ".jpeg"):
            async with aiofiles.open(path, "rb") as f:
                image = Image.open(io.BytesIO(await f.read())).convert("RGB")
            result = await _mineru_client.aio_two_step_extract(image)  # type: ignore[union-attr]
            return _to_md(result)

        if ext == ".pdf":
            try:
                import fitz  # PyMuPDF
            except ImportError:
                return "[PDF support requires PyMuPDF: uv add pymupdf]"
            doc = fitz.open(str(path))
            pages: list[str] = []
            for page in doc:
                pix = page.get_pixmap(dpi=150)
                image = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                result = await _mineru_client.aio_two_step_extract(image)  # type: ignore[union-attr]
                pages.append(_to_md(result))
            doc.close()
            return "\n\n---\n\n".join(pages)

        return f"[Unsupported format for MinerU VL: {ext}]"

    @staticmethod
    def _mineru_cli(path: Path) -> str:
        """Fallback: spawn mineru CLI (reloads model each call — only used when vLLM engine unavailable)."""
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "out"
            result = subprocess.run(
                ["mineru", "-p", str(path), "-o", str(out_dir), "-b", "pipeline"],
                capture_output=True, text=True, timeout=120,
            )
            md_files = list(out_dir.rglob("*.md"))
            if not md_files:
                logger.warning("mineru CLI produced no output for %s: %s", path.name, result.stderr[:200])
                return f"[MinerU extraction failed for {path.name}]"
            return md_files[0].read_text(errors="replace").strip()


class DetectPIITool(BaseTool):
    """PIIAgent tool: reuses ravi PIIDetectionGuardrail regex patterns."""

    def __init__(self) -> None:
        super().__init__(
            name="detect_pii",
            description="Scan text for PII (emails, phones, SSNs, credit cards)",
            input_schema={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
            risk=ToolRisk.SENSITIVE,
        )
        self._patterns = _PII_PATTERNS

    async def execute(self, text: str) -> ToolResult:  # type: ignore[override]
        findings: dict[str, int] = {}
        for label, pattern in self._patterns.items():
            matches = pattern.findall(text)
            if matches:
                findings[label] = len(matches)
        risk = "high" if findings else "clean"
        summary = (
            f"Found: {', '.join(f'{v}x {k}' for k, v in findings.items())}"
            if findings
            else "No PII detected"
        )
        return ToolResult(
            content=[TextBlock(text=summary)],
            app_data={"findings": findings, "risk_level": risk, "clean": not findings},
        )


class ClassifyDocumentTool(BaseTool):
    """ClassifierAgent tool: keyword-based document classification."""

    _CATEGORIES: dict[str, set[str]] = {
        "invoice": {
            "invoice", "bill to", "amount due", "payment due", "subtotal",
            "purchase order", "po number", "net 30", "remittance", "vendor",
            "invoice number", "due date", "quantity", "unit price",
        },
        "credit_statement": {
            "credit card", "credit score", "credit limit", "statement balance",
            "minimum payment", "apr", "credit bureau", "available credit",
            "payment due date", "credit account",
        },
        "receipt": {
            "receipt", "thank you for your purchase", "cashier", "store #",
            "change due", "total paid", "subtotal", "tax", "order #",
        },
        "contract": {
            "agreement", "contract", "whereas", "parties agree", "obligations",
            "indemnify", "liability", "termination", "governing law",
            "intellectual property", "confidential",
        },
        "report": {
            "executive summary", "findings", "recommendations", "analysis",
            "conclusion", "methodology", "appendix", "table of contents",
        },
    }

    def __init__(self) -> None:
        super().__init__(
            name="classify_document",
            description="Classify a document as invoice, credit statement, receipt, contract, report, or unknown",
            input_schema={
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "filename": {"type": "string"},
                },
                "required": ["text", "filename"],
            },
            risk=ToolRisk.SAFE,
        )

    async def execute(self, text: str, filename: str) -> ToolResult:  # type: ignore[override]
        text_lower = text.lower()
        scores = {
            cat: sum(1 for kw in kws if kw in text_lower)
            for cat, kws in self._CATEGORIES.items()
        }
        best = max(scores, key=scores.get)  # type: ignore[arg-type]
        best_score = scores[best]
        if best_score == 0:
            doc_type, confidence = "unknown", 0.0
        else:
            doc_type = best
            total = sum(scores.values()) or 1
            confidence = round(best_score / total, 2)
        return ToolResult(
            content=[TextBlock(text=f"{doc_type} ({confidence:.0%} confidence)")],
            app_data={"type": doc_type, "confidence": confidence, "scores": scores},
        )


class GenerateEmbeddingTool(BaseTool):
    """EmbeddingAgent tool: generate and persist embeddings without an LLM.

    Tries (in order):
      1. sentence-transformers all-MiniLM-L6-v2  (~80 MB, 384-dim)
      2. sklearn TF-IDF                          (128-dim sparse)
      3. Word-hash vector                        (64-dim, pure stdlib)
    """

    def __init__(self) -> None:
        super().__init__(
            name="generate_embedding",
            description="Generate a document embedding and persist to the embeddings/ folder",
            input_schema={
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "job_id": {"type": "string"},
                    "filename": {"type": "string"},
                },
                "required": ["text", "job_id", "filename"],
            },
            risk=ToolRisk.SAFE,
        )

    async def execute(  # type: ignore[override]
        self, text: str, job_id: str, filename: str
    ) -> ToolResult:
        result = await asyncio.to_thread(self._embed_and_store, text, job_id, filename)
        return ToolResult(
            content=[TextBlock(text=f"Stored {result['dimensions']}-dim ({result['method']})")],
            app_data=result,
        )

    @staticmethod
    def _embed_and_store(text: str, job_id: str, filename: str) -> dict[str, Any]:
        snippet = text[:2000]
        embedding, method = GenerateEmbeddingTool._compute(snippet)
        record = {
            "job_id": job_id,
            "filename": filename,
            "method": method,
            "dimensions": len(embedding),
            "text_preview": text[:200],
            "embedding": embedding,
            "created_at": time.time(),
        }
        out = EMBEDDINGS_DIR / f"{job_id}.json"
        out.write_text(json.dumps(record, indent=2))
        return {
            "method": method,
            "dimensions": len(embedding),
            "file": str(out),
            "preview": embedding[:5],
        }

    @staticmethod
    def _compute(text: str) -> tuple[list[float], str]:
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
            model = SentenceTransformer("all-MiniLM-L6-v2")
            vec: list[float] = model.encode(text).tolist()
            return vec, "sentence-transformers/all-MiniLM-L6-v2 (384-dim)"
        except ImportError:
            pass
        try:
            from sklearn.feature_extraction.text import TfidfVectorizer  # type: ignore
            vect = TfidfVectorizer(max_features=128)
            vec = vect.fit_transform([text]).toarray()[0].tolist()
            return vec, "sklearn TF-IDF (128-dim)"
        except ImportError:
            pass
        import hashlib
        words = re.findall(r"\b\w+\b", text.lower())[:500]
        vec = [0.0] * 64
        for w in words:
            idx = int(hashlib.md5(w.encode()).hexdigest(), 16) % 64
            vec[idx] += 1.0
        norm = (sum(x ** 2 for x in vec) ** 0.5) or 1.0
        return [x / norm for x in vec], "word-hash vector (64-dim)"


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------

catalog = AgentCatalog()
catalog.register_tool(ValidateFileTool())
catalog.register_tool(ExtractTextTool())
catalog.register_tool(DetectPIITool())
catalog.register_tool(ClassifyDocumentTool())
catalog.register_tool(GenerateEmbeddingTool())

logger.debug("Registered %d tools in catalog", len(list(catalog.all_tools())))
