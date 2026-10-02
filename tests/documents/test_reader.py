"""``Reader``: the choice of engine, isolation, limits, and the document server. Real worker processes throughout."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import textwrap
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from substrate.documents import Reader, ReadLimits
from substrate.documents.reading.pool import WorkerFailure, WorkerPool
from substrate.documents.types import ExtractionResult
from tests.documents._files import fixture, zip_bomb_docx

HEADER = {"filename": "doc.pdf", "content_type": None, "strategy": "auto", "limits": ReadLimits().model_dump(), "ocr": "none", "languages": ["eng"], "timeout_s": 30}


# ----------------------------------------------------------------------------------------------------------- choosing an engine


def test_a_location_and_an_engine_are_exclusive() -> None:
    with pytest.raises(ValueError, match="not both"):
        Reader("http://x", engine=Reader())


async def test_an_unknown_strategy_is_a_programming_error() -> None:
    with pytest.raises(ValueError, match="strategy"):
        await Reader().read(b"x", "a.txt", strategy="turbo")  # type: ignore[arg-type]


def test_a_custom_ocr_runs_in_this_process_only() -> None:
    class Mine:
        name = "mine"

        def recognize(self, png, *, languages):
            from substrate.documents.types import OcrResult

            return OcrResult(text="Invoice 4417", confidence=99.0)

    with pytest.raises(ValueError, match="isolate=False"):
        Reader(ocr=Mine())  # type: ignore[arg-type]


async def test_a_custom_ocr_reads_the_scanned_page_when_not_isolated() -> None:
    from substrate.documents.types import OcrResult

    class Mine:
        name = "mine"

        def recognize(self, png, *, languages):
            return OcrResult(text="Invoice 4417 from my engine", confidence=99.0)

    result = await Reader(ocr=Mine(), isolate=False).read(fixture("scanned_page.pdf"), "s.pdf")  # type: ignore[arg-type]
    assert "from my engine" in result.pages[0].text and result.pages[0].method == "ocr" and not result.needs_ocr


async def test_an_engine_is_delegated_to() -> None:
    class Stub:
        async def read(self, data, filename, *, content_type=None, strategy="auto"):
            return ExtractionResult(engine=f"stub:{strategy}:{filename}")

    result = await Reader(engine=Stub()).read(b"", "z.pdf", strategy="fast")
    assert result.engine == "stub:fast:z.pdf"


async def test_hi_res_without_a_document_server_says_it_degraded() -> None:
    result = await Reader().read(fixture("test_invoice.pdf"), "i.pdf", strategy="hi_res")
    assert result.success and result.degraded_from == "hi_res" and any("hi_res" in w for w in result.warnings)


# ------------------------------------------------------------------------------------------------------------------- isolation


@pytest.mark.parametrize("name", ["test_invoice.pdf", "sample.docx", "sample.xlsx", "bookmarks.pdf"])
async def test_a_worker_and_a_thread_read_the_same_document_the_same_way(name: str) -> None:
    isolated = await Reader().read(fixture(name), name)
    in_process = await Reader(isolate=False).read(fixture(name), name)
    assert isolated.success and isolated.markdown == in_process.markdown and isolated.engine == in_process.engine


async def test_reading_concurrently_in_workers() -> None:
    results = await asyncio.gather(*(Reader().read(fixture("bookmarks.pdf"), "b.pdf") for _ in range(8)))
    assert all(r.success for r in results) and len({r.markdown for r in results}) == 1


def test_the_host_never_loads_the_pdf_parser_when_isolated() -> None:
    program = textwrap.dedent(
        """
        import asyncio, sys
        from substrate.documents import Reader
        data = open("tests/fixtures/test_invoice.pdf", "rb").read()
        result = asyncio.run(Reader().read(data, "i.pdf"))
        assert result.success, result.error
        assert "pypdfium2" not in sys.modules, "the host process loaded the PDF parser"
        assert "rapidocr" not in sys.modules and "onnxruntime" not in sys.modules
        """
    )
    done = subprocess.run([sys.executable, "-c", program], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr


def test_a_worker_that_hangs_is_killed_at_the_deadline_and_the_pool_carries_on() -> None:
    hang = WorkerPool(1, command=[sys.executable, "-c", "import time; time.sleep(600)"])
    started = time.monotonic()
    with pytest.raises(WorkerFailure) as caught:
        hang.run(HEADER, b"data", timeout_s=1.0)
    assert caught.value.timed_out and time.monotonic() - started < 15
    hang.close()
    healthy = WorkerPool(1)
    for _ in range(2):
        assert ExtractionResult.model_validate_json(healthy.run(HEADER, fixture("test_invoice.pdf"), timeout_s=30)).success
    healthy.close()


def test_a_worker_that_dies_is_reported() -> None:
    dead = WorkerPool(1, command=[sys.executable, "-c", "import os; os._exit(3)"])
    with pytest.raises(WorkerFailure, match="died"):
        dead.run(HEADER, b"data", timeout_s=10)
    dead.close()


def test_workers_are_recycled_after_a_number_of_documents(monkeypatch) -> None:
    from substrate.documents.reading import pool as pool_module

    monkeypatch.setattr(pool_module, "RECYCLE_AFTER", 2)
    pool = WorkerPool(1)
    seen = set()
    for _ in range(5):
        pool.run(HEADER, fixture("test_invoice.pdf"), timeout_s=30)
        seen.add(pool._idle[0].proc.pid if pool._idle else None)
    pool.close()
    assert len(seen - {None}) >= 2


async def test_a_memory_bomb_ends_the_worker_not_the_host_and_the_next_read_is_fine() -> None:
    reader = Reader(limits=ReadLimits(memory_bytes=4 * 1024 * 1024), workers=1)
    result = await reader.read(fixture("sample.xlsx"), "s.xlsx")
    assert isinstance(result, ExtractionResult) and (result.success or result.error)
    assert (await Reader().read(fixture("test_invoice.pdf"), "i.pdf")).success


async def test_a_hostile_document_through_the_reader_is_a_failed_result() -> None:
    result = await Reader().read(zip_bomb_docx(), "evil.docx")
    assert not result.success and result.error


# ------------------------------------------------------------------------------------------------------------- document server


class _Server:
    def __init__(self, status: int, body: dict | None = None) -> None:
        self.status, self.body, self.requests = status, body or {}, []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:  # noqa: D102
                pass

            def do_POST(self) -> None:  # noqa: N802
                outer.requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                payload = json.dumps(outer.body).encode()
                self.send_response(outer.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self.http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.http.server_address[1]}"

    def close(self) -> None:
        self.http.shutdown()
        self.http.server_close()


async def test_the_server_answer_is_final_even_when_it_refuses() -> None:
    server = _Server(200, ExtractionResult(success=False, error="blocked by the security scan", engine="doc-intel").model_dump(mode="json"))
    try:
        result = await Reader(server.url).read(fixture("test_invoice.pdf"), "i.pdf", strategy="hi_res")
    finally:
        server.close()
    assert not result.success and "security scan" in result.error and result.engine == "doc-intel"
    assert server.requests[0]["strategy"] == "hi_res" and server.requests[0]["filename"] == "i.pdf"


async def test_a_server_that_is_down_falls_back_to_the_built_in_reader_and_says_so() -> None:
    server = _Server(200)
    url = server.url
    server.close()  # nothing listens here now
    result = await Reader(url).read(fixture("test_invoice.pdf"), "i.pdf")
    assert result.success and result.degraded_from == url and any("unreachable" in w for w in result.warnings)
    refused = await Reader(url, fallback=False).read(fixture("test_invoice.pdf"), "i.pdf")
    assert not refused.success and refused.degraded_from == "unreachable"


async def test_a_server_error_is_unreachable_but_a_client_error_is_not() -> None:
    boom, bad = _Server(503), _Server(422, {"detail": "unreadable"})
    try:
        assert (await Reader(boom.url).read(fixture("test_invoice.pdf"), "i.pdf")).success  # 5xx → fell back locally
        refused = await Reader(bad.url).read(fixture("test_invoice.pdf"), "i.pdf")
    finally:
        boom.close()
        bad.close()
    assert not refused.success and refused.degraded_from is None
