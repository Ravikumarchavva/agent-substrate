"""Documents: read what a user hands an agent, and let the model navigate it.

``Reader`` turns a file into markdown pages — PDF, Word, PowerPoint, Excel, OpenDocument, HTML, Markdown, CSV — with headings, tables and
page markers, using only the base install (PDFium, and the standard library for Office). A scan with no text layer is OCR'd if Tesseract or
RapidOCR is there and otherwise *reported* (``needs_ocr``), never silently empty.

``Library`` files documents in a folder the way a person would — an index, one file per section — and keeps a catalog beside it, so the model
works through them like a folder: ``list``, ``outline``, ``read``, ``find``, ``view``. No embeddings for a conversation's few documents; give
``Library`` an ``embedder=`` (a URL is enough) for a large knowledge base and it searches by meaning too.

    uv run python examples/10_documents.py
"""

from __future__ import annotations

import asyncio

from _model import ToolCall, pick_model

from substrate.agents import ReActAgent
from substrate.documents import DocumentsTool, Library, Reader
from substrate.runtime import Runtime
from substrate.stores import Store

HANDBOOK = b"""# Employee Handbook

## Refunds

Customers may return damaged goods within thirty days for a full refund. Requests go to the support desk, which replies within one
working day. """ + b"Refunds are issued to the original payment method. " * 40 + b"""

## Shipping

Orders to Rotterdam ship from the Dutch warehouse in two days. """ + b"Carriers collect parcels every afternoon. " * 40

COLLECTION = "conversations/demo/documents"


async def main() -> None:
    # 1. Read: the same call for any format. ``result.markdown`` has a ``<!-- page N -->`` marker before each page.
    result = await Reader().read(HANDBOOK, "handbook.md")
    print(f"read {len(result.pages)} page(s) with the {result.engine} engine; needs OCR: {result.needs_ocr or 'nothing'}")

    store = Store.at("./.substrate")
    async with store:
        # 2. File it: an OKF bundle in the store's files, a catalog in its database.
        library = Library(store)
        added = await library.add(HANDBOOK, "handbook.md", collection=COLLECTION)
        print(f"filed {added.document}: {added.sections} sections")

        # 3. Let the model work through it. Which collection it can reach comes from the run's scope, never from the model.
        tool = DocumentsTool(library, collection=lambda scope: COLLECTION)
        agent = ReActAgent(
            "reader",
            model=pick_model(
                ToolCall("documents", {"action": "find", "query": "refund damaged goods"}),
                ToolCall("documents", {"action": "read", "document": added.document, "section": 1}),
                "Damaged goods can be returned within thirty days for a full refund [1].",
            ),
            tools=[tool],
            system_instructions="Answer from the documents tool, and cite what you read as [n].",
        )
        async with Runtime.open("./.substrate") as runtime:
            outcome = await runtime.run(agent, "Can I return damaged goods?")
            print(outcome.output)

        await library.delete(collection=COLLECTION, document=added.document)  # leave the folder as we found it


if __name__ == "__main__":
    asyncio.run(main())
