"""Invariant register — reading documents (rows I32–I34).

What a user hands an agent is untrusted bytes. Three promises hold however the document is built: a page that is only a picture is never
returned silently empty, a parser crash or a memory bomb cannot reach the host, and no hostile file makes ``Reader`` raise.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest

from substrate.documents import Reader
from substrate.documents.types import ReadLimits
from tests.documents._files import (
    deeply_nested_docx,
    entity_bomb_docx,
    external_entity_docx,
    fixture,
    many_members_docx,
    zip_bomb_docx,
)


async def test_a_page_that_is_only_a_picture_is_recognised_or_reported_never_silently_empty() -> None:
    """A scanned page comes back with its text (OCR available) or listed in ``needs_ocr`` (it is not) — never as an empty page with no note."""
    for ocr in ("auto", None):
        result = await Reader(ocr=ocr, isolate=False).read(fixture("scanned_page.pdf"), "scan.pdf")
        page = result.pages[0]
        assert result.success and ("4417" in page.text or (page.needs_ocr and 1 in result.needs_ocr))


def test_an_isolated_read_never_loads_the_parser_or_the_ocr_runtime_into_the_host() -> None:
    """With isolation on (the default) the PDF parser and the OCR runtime run in a worker process: the host's modules never include them."""
    program = textwrap.dedent(
        """
        import asyncio, sys
        from substrate.documents import Reader
        data = open("tests/fixtures/documents/scanned_page.pdf", "rb").read()
        assert asyncio.run(Reader().read(data, "s.pdf")).success
        loaded = {m.split(".")[0] for m in sys.modules}
        assert not loaded & {"pypdfium2", "rapidocr", "onnxruntime", "cv2"}, loaded & {"pypdfium2", "rapidocr", "onnxruntime", "cv2"}
        """
    )
    done = subprocess.run([sys.executable, "-c", program], capture_output=True, text=True, timeout=180)
    assert done.returncode == 0, done.stderr


@pytest.mark.parametrize("make", [zip_bomb_docx, entity_bomb_docx, external_entity_docx, deeply_nested_docx, many_members_docx])
async def test_a_hostile_document_is_a_failed_result_never_an_exception(make) -> None:
    """A zip bomb, an entity bomb, an external entity, absurd nesting and a member flood each come back as ``success=False`` with a reason."""
    result = await Reader(limits=ReadLimits(timeout_s=20)).read(make(), "evil.docx")
    assert not result.success and result.error
