"""The built-in engine on every format it reads, using real files (``tests/fixtures/documents``, made by Word-compatible and
OpenDocument software) and files built to hurt it. These run the engine directly; ``test_reader.py`` runs it through ``Reader``."""

from __future__ import annotations

import pytest

from substrate.documents.reading.engine import read_document
from substrate.documents.reading.sniff import Format, sniff
from substrate.documents.types import ReadLimits
from tests.documents._files import (
    deeply_nested_docx,
    entity_bomb_docx,
    external_entity_docx,
    fixture,
    many_members_docx,
    with_encryption_flag,
    zip_bomb_docx,
)


def read(name_or_bytes, filename: str = "", **kw):
    data = fixture(name_or_bytes) if isinstance(name_or_bytes, str) else name_or_bytes
    return read_document(
        data,
        filename or (name_or_bytes if isinstance(name_or_bytes, str) else ""),
        **kw,
    )


def lines(result) -> list[str]:
    return [line for line in result.markdown.splitlines() if line.strip()]


# ------------------------------------------------------------------------------------------------------------ Word


@pytest.mark.parametrize("name", ["sample.docx", "sample.odt"])
def test_a_word_processing_document_keeps_its_structure(name: str) -> None:
    result = read(name)
    assert (
        result.success
        and result.engine == "native"
        and result.title == "Quarterly Operations Report"
    )
    md = result.markdown
    assert (
        "# Quarterly Operations Report" in md
        and "# Revenue" in md
        and "## Regional detail" in md
    )
    assert (
        "- Direct sales" in md
        and "- Partner channel" in md
        and "1. First step" in md
        and "1. Second step" in md
    )
    assert (
        "| Region | Q2 | Q3 |" in md
        and "| EMEA | 120 | 140 |" in md
        and "| --- | --- | --- |" in md
    )
    assert "Invoice 4417 was settled in full." in result.pages[0].text
    # the explicit page break makes a second page that starts with its heading
    assert [p.page_number for p in result.pages] == [1, 2]
    assert (
        result.pages[1].markdown.startswith("# Risks")
        and "Rotterdam" in result.pages[1].text
    )
    assert (
        md.index("<!-- page 1 -->") < md.index("<!-- page 2 -->") < md.index("# Risks")
    )


def test_plain_text_is_the_text_without_the_markup() -> None:
    page = read("sample.docx").pages[0]
    assert (
        "#" not in page.text
        and "|" not in page.text
        and "Region Q2 Q3" in " ".join(page.text.split())
    )


# ------------------------------------------------------------------------------------------------------------ slides


@pytest.mark.parametrize("name", ["sample.pptx", "sample.odp"])
def test_each_slide_is_a_page_with_its_title_bullets_table_and_notes(name: str) -> None:
    result = read(name)
    assert result.success and result.title == "Launch Plan" and len(result.pages) == 3
    first, second, third = (p.markdown for p in result.pages)
    assert first.startswith("## Launch Plan") and "Autumn campaign" in first
    assert (
        "- Design freeze" in second
        and "- Beta release" in second
        and "  - Invite 50 customers" in second
    )
    assert "> Notes: Mention the Rotterdam warehouse." in second
    assert (
        third.startswith("## Budget")
        and "| Item | EUR |" in third
        and "| Ads | 12000 |" in third
    )


# ------------------------------------------------------------------------------------------------------------ sheets


@pytest.mark.parametrize("name", ["sample.xlsx", "sample.ods"])
def test_each_visible_sheet_is_a_page_with_cached_values_and_iso_dates(
    name: str,
) -> None:
    result = read(name)
    assert result.success and len(result.pages) == 2
    sales = result.pages[0].markdown
    assert (
        sales.startswith("## Sales")
        and "| Month | Units | Price | Revenue | Closed on |" in sales
    )
    assert (
        "| Jan | 10 | 2.5 | 25 | 2026-01-31 |" in sales
        and "| Feb | 12 | 2.5 | 30 | 2026-02-28 |" in sales
    )  # formulas show their cached value
    assert (
        "Totals exclude returns \\| pipes and newlines" in result.pages[1].markdown
    )  # a pipe and a newline cannot break the table
    assert "do not show" not in result.markdown and any(
        "hidden sheet" in w for w in result.warnings
    )


# ------------------------------------------------------------------------------------------------------------ text formats


def test_html_keeps_text_links_lists_and_tables_and_drops_what_can_run() -> None:
    html = (
        b"<!doctype html><html><head><title>Hello</title><script>alert(1)</script><style>p{}</style></head><body>"
        b"<h1>Main &amp; title</h1><p>See <a href='https://ex.com/x'>the page</a> and <a href='javascript:alert(1)'>a trap</a>.</p>"
        b"<ul><li>one</li><li>two<ul><li>nested</li></ul></li></ul><table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2 | 3</td></tr></table>"
        b"<iframe src='http://evil'>frame text</iframe><img src='x.png' alt='a chart'><select><option>a<option>b</select><p>tail</p></body></html>"
    )
    result = read(html, "page.html")
    md = result.markdown
    assert result.title == "Hello" and "# Main & title" in md
    assert (
        "[the page](https://ex.com/x)" in md
        and "javascript:" not in md
        and "a trap" in md
    )
    assert (
        "- one" in md
        and "  - nested" in md
        and "| 1 | 2 \\| 3 |" in md
        and "[image: a chart]" in md
    )
    assert (
        "alert" not in md
        and "frame text" not in md
        and "evil" not in md
        and md.rstrip().endswith("tail")
    )


def test_markdown_text_csv_tsv_and_json() -> None:
    assert (
        "# Title" in read(b"# Title\n\nbody", "n.md").markdown
        and read(b"# Title\n\nbody", "n.md").title == "Title"
    )
    assert "plain words" in read(b"plain words here", "n.txt").markdown
    csv = read(b"name;qty\nbolt;4\nnut;9\n", "t.csv").markdown
    assert "| name | qty |" in csv and "| bolt | 4 |" in csv
    assert "| a | b |" in read(b"a\tb\n1\t2\n", "t.tsv").markdown
    assert (
        '"x": 1' in read(b'{"x":1}', "d.json").markdown
        and "```json" in read(b'{"x":1}', "d.json").markdown
    )
    assert (
        "café" in read("café".encode("cp1252"), "legacy.txt").markdown
    )  # not UTF-8: Windows-1252 rather than mojibake


def test_tables_are_capped_and_say_so() -> None:
    rows = "\n".join(f"{i},{i * 2}" for i in range(1500))
    result = read(("h1,h2\n" + rows).encode(), "big.csv")
    assert result.markdown.count("\n| ") <= 1002 and any(
        "truncated" in w for w in result.warnings
    )


# ------------------------------------------------------------------------------------------------------------ sniffing


def test_the_bytes_decide_not_the_label() -> None:
    docx = fixture("sample.docx")
    assert (
        sniff(docx, "", "application/octet-stream")
        == Format.DOCX
        == sniff(docx, "report.pdf", "application/pdf")
    )
    assert (
        sniff(fixture("sample.xlsx")) == Format.XLSX
        and sniff(fixture("sample.odt")) == Format.ODT
    )
    assert sniff(fixture("test_invoice.pdf"), "x.txt") == Format.PDF
    assert (
        sniff(b"RIFF\x00\x00\x00\x00WEBPVP8 ", "p.bin", "application/octet-stream")
        == Format.IMAGE
    )
    assert (
        sniff(b"<p>fragment</p>", "", "") == Format.HTML
        and sniff(b"hello", "", "") == Format.TEXT
    )
    assert sniff(b"\xd0\xcf\x11\xe0" + bytes(100), "old.doc") == Format.OLE
    assert sniff(bytes(range(256)) * 20, "x.docx") == Format.UNKNOWN


def test_unsupported_things_are_refused_with_a_reason_that_helps() -> None:
    old = read(b"\xd0\xcf\x11\xe0" + bytes(200), "legacy.doc")
    assert (
        not old.success
        and "legacy" in old.error
        and "docx" in old.error
        and "document server" in old.error
    )
    blob = read(bytes(range(256)) * 20, "x.bin")
    assert not blob.success and "does not look like a document" in blob.error
    assert not read(b"", "empty.pdf").success and "empty" in read(b"", "e").error


# ------------------------------------------------------------------------------------------------------------ hostile files


@pytest.mark.parametrize(
    ("make", "reason"),
    [
        (zip_bomb_docx, "zip bomb|inflates|compressed"),
        (entity_bomb_docx, "entity"),
        (external_entity_docx, "entity"),
        (deeply_nested_docx, "deeply nested"),
        (many_members_docx, "members"),
        (lambda: with_encryption_flag(many_members_docx(5)), "encrypted"),
    ],
    ids=[
        "zip-bomb",
        "entity-bomb",
        "external-entity",
        "deep-nesting",
        "member-flood",
        "encrypted",
    ],
)
def test_a_hostile_office_file_is_refused_with_a_reason_and_never_raises(
    make, reason: str
) -> None:
    import re

    result = read(make(), "evil.docx")
    assert isinstance(result.error, str) and not result.success
    assert re.search(reason, result.error, re.IGNORECASE), result.error


def test_an_external_entity_never_reads_a_file() -> None:
    result = read(external_entity_docx(), "evil.docx")
    assert "root:" not in result.markdown and "root:" not in (result.error or "")


@pytest.mark.parametrize(
    "name",
    ["sample.docx", "sample.xlsx", "sample.pptx", "sample.odt", "test_invoice.pdf"],
)
def test_a_truncated_or_corrupted_file_is_a_failure_not_an_exception(name: str) -> None:
    data = fixture(name)
    for broken in (
        data[: len(data) // 2],
        data[:100],
        data[: len(data) // 2] + bytes(range(256)) * 20 + data[len(data) // 2 :],
    ):
        result = read(broken, name)
        assert (
            result.success or result.error
        )  # whatever it makes of it, it never raises, and a failure says why


def test_limits_are_enforced() -> None:
    big = read(b"x" * 5000, "a.txt", limits=ReadLimits(max_bytes=1000))
    assert not big.success and "limit" in big.error
    capped = read(fixture("sample.xlsx"), limits=ReadLimits(max_pages=1))
    assert len(capped.pages) == 1 and any("first 1 of 2" in w for w in capped.warnings)
