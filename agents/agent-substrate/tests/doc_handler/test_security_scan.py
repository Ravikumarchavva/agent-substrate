"""scan_document — real doc-firewall inference against real PDF bytes.

Verified findings from building this wrapper (not assumed): a clean
committed fixture scores ALLOW/0.0 risk; a synthetically constructed PDF
carrying an OpenAction JavaScript annotation scores FLAG/~0.88 with
concrete /JavaScript, /JS, and active-content findings — real detection.
"""

from __future__ import annotations

from pathlib import Path

from substrate.doc_handler.security_scan import scan_document
from substrate.kernel.agent.safety import Severity

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def test_clean_pdf_is_not_flagged():
    data = (FIXTURES / "test_invoice.pdf").read_bytes()
    v = scan_document(data, filename="test_invoice.pdf")
    assert v.severity == Severity.NONE
    assert not v.flagged
    assert v.detector == "doc_firewall"
    assert v.modality == "document"


def test_pdf_with_javascript_action_is_flagged():
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.add_js('app.alert("test");')

    import io

    buf = io.BytesIO()
    writer.write(buf)
    data = buf.getvalue()

    v = scan_document(data, filename="malicious.pdf")
    assert v.severity != Severity.NONE
    assert v.flagged
    assert v.scores["risk_score"] > 0
    assert "javascript" in v.detail.lower() or "js" in v.detail.lower()


def test_undecodable_bytes_fail_open_not_raise():
    v = scan_document(
        b"not a real document, just garbage bytes", filename="garbage.pdf"
    )
    assert v.severity == Severity.NONE
    assert not v.flagged


def test_empty_bytes_fail_open_not_raise():
    v = scan_document(b"", filename="empty.pdf")
    assert v.severity == Severity.NONE
    assert not v.flagged
