from dotenv import load_dotenv
from substrate.config import SubstrateConfig

load_dotenv()  # walks up to find the repo-root .env
settings = SubstrateConfig()

"""Example 08-3: Production-grade invoice extraction using InvoiceExtractorTool.

Demonstrates InvoiceExtractorTool for extracting text and tables from invoice files
(PDF, TIF, PNG, JPG). Sections cover:

  1. Tool setup and direct file extraction
  2. ReActAgent integration with structured output
  3. Batch extraction with asyncio.gather
  4. Error handling — missing file, unsupported format, out-of-range page

Install optional dependencies first:
  uv sync --group optional
  sudo apt-get install tesseract-ocr   # Linux
  brew install tesseract               # macOS
"""

import asyncio
import importlib
import shutil
import tempfile
from pathlib import Path

from substrate.capabilities.tools.invoice_extractor.tool import InvoiceExtractorTool
from substrate.agents.core import ReActAgent
from substrate.integrations.llm.factory import create_model_client
from substrate.kernel.agent_catalog import AgentCatalog
from substrate.agents.context import InMemoryHistoryProvider

# Infrastructure: none required for direct tool calls.
#   For the agent sections, set OPENAI_API_KEY (or another provider key).

# Sample invoice files shipped with the repo
PUBLIC_DIR = Path(__file__).parent.parent.parent / "public"

# ---


def section_0_install_check() -> None:
    """Section 0 — Verify optional OCR/PDF dependencies."""
    print("=== Section 0: Dependency check ===")

    for pkg in ("pdfplumber", "PIL", "pytesseract"):
        found = importlib.util.find_spec(pkg) is not None
        mark = "✓" if found else "✗"
        print(f"  {mark}  {pkg}")

    tesseract = shutil.which("tesseract")
    mark = "✓" if tesseract else "✗"
    print(f"  {mark}  tesseract binary: {tesseract or 'NOT FOUND'}")

    if not tesseract:
        print("  Install: sudo apt-get install tesseract-ocr  (Linux)")
        print("           brew install tesseract               (macOS)")
    print()


# ---


async def section_1_direct_extraction(tool: InvoiceExtractorTool) -> None:
    """Section 1 — Direct tool call on real invoice files from public/."""
    print("=== Section 1: Direct file extraction ===")

    tif_files = sorted(PUBLIC_DIR.glob("*.tif")) + sorted(PUBLIC_DIR.glob("*.tiff"))
    png_files = sorted(PUBLIC_DIR.glob("*.png"))
    all_files = tif_files + png_files

    if not all_files:
        print(f"  No invoice files found in {PUBLIC_DIR}")
        print("  Expected: *.tif / *.png files in agent-substrate/public/")
        return

    print(f"  Found {len(all_files)} file(s): {[f.name for f in all_files]}")
    print()

    for invoice_file in all_files[:2]:  # process first two
        result = await tool.execute(file_path=str(invoice_file))
        print(f"  --- {invoice_file.name} ---")
        if result.is_error:
            print(f"  ERROR: {result.content[0].text}")
        else:
            text = result.content[0].text
            words = len(text.split())
            print(f"  Extracted {words} words, {len(text)} chars")
            print(f"  Preview: {text.strip()[:200]!r}")
        print()


# ---


async def section_2_agent_extraction(tool: InvoiceExtractorTool) -> None:
    """Section 2 — ReActAgent with InvoiceExtractorTool and structured output."""
    print("=== Section 2: ReActAgent extraction ===")

    api_keys = {
        "openai": settings.OPENAI_API_KEY,
        "anthropic": settings.ANTHROPIC_API_KEY,
        "google": settings.GEMINI_API_KEY,
        "groq": settings.GROQ_API_KEY,
        "openrouter": settings.OPENROUTER_API_KEY,
    }

    if not any(api_keys.values()):
        print("  No API key configured — skipping agent section.")
        print("  Set OPENAI_API_KEY to run this section.")
        return

    from pydantic import BaseModel

    class LineItem(BaseModel):
        description: str
        quantity: float
        unit_price: float
        total: float

    class Invoice(BaseModel):
        invoice_number: str
        date: str
        vendor: str
        line_items: list[LineItem]
        total: float

    catalog = AgentCatalog()
    catalog.register_model(
        "primary",
        create_model_client(settings.CHAT_MODEL, api_keys=api_keys),
    )
    catalog.register_memory("memory", InMemoryHistoryProvider())
    catalog.register_tool(tool)

    agent = ReActAgent(
        name="InvoiceAgent",
        description="Extracts structured data from invoice files.",
        catalog=catalog,
        system_instructions=(
            "You are an invoice processing assistant. "
            "When given an invoice file path, use invoice_extractor to read it "
            "and return structured invoice data."
        ),
        max_iterations=5,
    )

    # --- 2a. Sample text description (no file needed) ---
    sample_prompt = (
        "Extract the invoice data: Invoice #INV-2024-001, date 2024-01-15, "
        "vendor: Acme Corp, items: Widget A x5 @ $10.00, total $50.00"
    )
    print(f"  Prompt: {sample_prompt[:80]}...")
    result = await agent.run(sample_prompt, response_schema=Invoice)
    print(f"  Output: {result.output_text[:300]}")
    if result.structured_output and result.structured_output.parsed:
        inv = result.structured_output.parsed
        print(
            f"  Structured: vendor={inv.vendor!r}  total={inv.total}  items={len(inv.line_items)}"
        )
    print()

    # --- 2b. Agent on a real file ---
    tif_files = sorted(PUBLIC_DIR.glob("*.tif"))
    if tif_files:
        file_path = tif_files[0]
        print(f"  Extracting from file: {file_path.name}")
        await agent.reset()
        file_result = await agent.run(
            f"Extract and summarise this invoice file: {file_path}",
            response_schema=Invoice,
        )
        print(f"  Output: {file_result.output_text[:300]}")


# ---


async def section_3_batch_extraction(tool: InvoiceExtractorTool) -> None:
    """Section 3 — Batch extraction with asyncio.gather."""
    print("=== Section 3: Batch extraction ===")

    tif_files = sorted(PUBLIC_DIR.glob("*.tif")) + sorted(PUBLIC_DIR.glob("*.png"))
    if len(tif_files) < 2:
        print(f"  Need at least 2 files for batch demo; found {len(tif_files)}")
        return

    file_paths = [str(f) for f in tif_files]
    print(
        f"  Processing {len(file_paths)} files in parallel: {[Path(p).name for p in file_paths]}"
    )

    results = await asyncio.gather(
        *[tool.execute(file_path=p) for p in file_paths],
        return_exceptions=True,
    )

    for path, result in zip(file_paths, results):
        name = Path(path).name
        if isinstance(result, Exception):
            print(f"  ✗  {name}: {result}")
        elif result.is_error:
            print(f"  ✗  {name}: {result.content[0].text[:80]}")
        else:
            words = len(result.content[0].text.split())
            print(f"  ✓  {name}: {words} words extracted")


# ---


async def section_4_error_handling(tool: InvoiceExtractorTool) -> None:
    """Section 4 — Error handling for common failure cases."""
    print("=== Section 4: Error handling ===")

    tmp = Path(tempfile.gettempdir())
    tif_files = sorted(PUBLIC_DIR.glob("*.tif"))

    cases = [
        ("Missing file", {"file_path": str(tmp / "does_not_exist.tif")}),
        ("Unsupported format", {"file_path": str(tmp / "invoice.txt")}),
    ]
    if tif_files:
        cases.append(
            ("Out-of-range page", {"file_path": str(tif_files[0]), "pages": [9999]})
        )

    for label, kwargs in cases:
        result = await tool.execute(**kwargs)
        status = "ERROR" if result.is_error else "OK"
        text = result.content[0].text[:100] if result.content else "(no content)"
        print(f"  [{status}] {label}: {text}")


# ---


async def main() -> None:
    section_0_install_check()

    tool = InvoiceExtractorTool()
    print(f"Tool: {tool.name!r}  category={tool.category!r}  risk={tool.risk.name}")
    print()

    await section_1_direct_extraction(tool)
    await section_2_agent_extraction(tool)
    await section_3_batch_extraction(tool)
    await section_4_error_handling(tool)


if __name__ == "__main__":
    asyncio.run(main())
