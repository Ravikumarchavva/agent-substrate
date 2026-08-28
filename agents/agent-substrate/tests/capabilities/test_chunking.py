from __future__ import annotations

from substrate.capabilities.knowledge.chunking import (
    StructureAwareChunker,
    get_chunker,
)


def test_structure_chunker_registered_in_factory() -> None:
    chunker = get_chunker("structure")
    assert isinstance(chunker, StructureAwareChunker)


def test_multiple_headings_produce_correct_section_ids() -> None:
    text = (
        "# Introduction\n"
        "This is the intro section. It has two sentences.\n\n"
        "## Background\n"
        "This is the background section. It also has two sentences.\n"
    )
    chunker = StructureAwareChunker(chunk_size=512, overlap=64)
    docs = chunker.chunk(text)

    assert len(docs) == 2
    assert docs[0].metadata["section_id"] == 0
    assert docs[1].metadata["section_id"] == 1
    assert "intro" in docs[0].to_text().lower()
    assert "background" in docs[1].to_text().lower()


def test_no_headings_falls_back_to_single_section() -> None:
    text = "Plain text with no markdown headings at all. Just prose. Nothing fancy."
    chunker = StructureAwareChunker(chunk_size=512, overlap=64)
    docs = chunker.chunk(text)

    assert len(docs) >= 1
    section_ids = {doc.metadata["section_id"] for doc in docs}
    assert section_ids == {0}


def test_prev_next_chunk_ids_linked_across_document() -> None:
    text = (
        "# Section One\n"
        + "Sentence one here. Sentence two here. Sentence three here. " * 10
        + "\n\n## Section Two\n"
        + "Another sentence. Yet another sentence. " * 10
    )
    chunker = StructureAwareChunker(chunk_size=200, overlap=40)
    docs = chunker.chunk(text)

    assert len(docs) > 2
    assert docs[0].metadata["prev_chunk_id"] is None
    assert docs[-1].metadata["next_chunk_id"] is None

    for i, doc in enumerate(docs):
        if i > 0:
            assert doc.metadata["prev_chunk_id"] == docs[i - 1].id
        if i < len(docs) - 1:
            assert doc.metadata["next_chunk_id"] == docs[i + 1].id


def test_protected_span_is_never_split() -> None:
    protected_text = "TABLE_START | col1 | col2 |\n| a | b |\n TABLE_END"
    text = (
        "Some intro sentence before the table. Another lead-in sentence here.\n\n"
        f"{protected_text}\n\n"
        "Some outro sentence after the table. Another trailing sentence here."
    )
    start = text.index(protected_text)
    end = start + len(protected_text)

    # Use a tiny chunk_size that would normally force the table to be split.
    chunker = StructureAwareChunker(chunk_size=20, overlap=5)
    docs = chunker.chunk(text, protected_spans=[(start, end)])

    matches = [doc for doc in docs if doc.to_text().strip() == protected_text.strip()]
    assert len(matches) == 1
    # The protected block must appear whole in exactly one chunk, never split
    # across multiple chunks or merged with a partial neighbour.
    for doc in docs:
        chunk_text = doc.to_text()
        if chunk_text.strip() != protected_text.strip():
            assert "TABLE_START" not in chunk_text
            assert "TABLE_END" not in chunk_text


def test_respects_chunk_size_and_overlap() -> None:
    long_text = "".join(
        f"This is sentence number {i} in a long document. " for i in range(50)
    )
    chunker = StructureAwareChunker(chunk_size=100, overlap=20)
    docs = chunker.chunk(long_text)

    assert len(docs) > 1
    for doc in docs[:-1]:
        # Allow a little slack: a single sentence longer than chunk_size
        # cannot be split further (packing operates on whole sentences).
        assert len(doc.to_text()) <= 150

    # Overlap should mean consecutive chunks share trailing/leading content.
    assert any(
        docs[i].to_text().split()[-1] in docs[i + 1].to_text()
        for i in range(len(docs) - 1)
    )


def _html_table(n_rows: int) -> str:
    header = "<tr><th>Date</th><th>Event</th><th>Venue</th></tr>"
    rows = "".join(
        f"<tr><td>Day {i}</td><td>Event {i}</td><td>Venue {i}</td></tr>"
        for i in range(n_rows)
    )
    return f"<table>{header}{rows}</table>"


def test_oversized_html_table_is_split_not_dropped() -> None:
    # A real shape seen from document-intelligence extraction: an HTML
    # table with no ". "/"! "/"? " boundaries wrapped in surrounding prose.
    table = _html_table(n_rows=80)  # comfortably over chunk_size on its own
    text = (
        "# Schedule\n"
        "Intro sentence before the table. Another lead-in sentence.\n\n"
        f"{table}\n\n"
        "Outro sentence after the table. Another trailing sentence.\n"
    )
    chunker = StructureAwareChunker(chunk_size=500, overlap=100)
    docs = chunker.chunk(text)

    assert all(len(doc.to_text()) <= 500 for doc in docs)

    table_docs = [doc for doc in docs if "| Date | Event | Venue |" in doc.to_text()]
    assert len(table_docs) > 1, "a table this large must split into multiple pieces"
    # Every piece keeps the header row, not just the first.
    for doc in table_docs:
        assert "| --- | --- | --- |" in doc.to_text()
    # No row's data was silently dropped between pieces.
    combined = " ".join(doc.to_text() for doc in table_docs)
    assert "Event 0" in combined
    assert "Event 79" in combined


def test_tiny_fragment_section_is_merged_not_a_standalone_chunk() -> None:
    """Real bug, found in production output: a whole chunk containing only
    "NEW K" (5 characters) -- two headings close together left almost
    nothing detected as the first one's body. That fragment must not
    become its own low-information chunk."""
    text = (
        "# Real Section\n"
        "This is a real paragraph with actual content worth retrieving on its own.\n\n"
        "## NEW K\n"
        "Content that legitimately belongs to the next real section starts here "
        "and continues for a while to make sure it reads naturally.\n"
    )
    chunker = StructureAwareChunker(chunk_size=512, overlap=64)
    docs = chunker.chunk(text)

    assert not any(doc.to_text().strip() == "NEW K" for doc in docs)
    # The fragment's heading text should still be findable, folded into
    # whichever neighboring chunk absorbed it -- not silently dropped.
    assert any("NEW K" in doc.to_text() for doc in docs)


def test_last_tiny_section_merges_backward() -> None:
    """A trailing fragment has no "next" section to merge into, so it must
    fold into the previous one instead of being dropped or left standalone."""
    text = (
        "# Main Section\n"
        "This is the real content of the document with a full paragraph here.\n\n"
        "## X\n"
    )
    chunker = StructureAwareChunker(chunk_size=512, overlap=64)
    docs = chunker.chunk(text)

    assert not any(doc.to_text().strip() == "X" for doc in docs)


def test_section_heading_is_prepended_to_its_first_chunk() -> None:
    """Real bug, found in production output: a chunk reading "is a suite of
    tools used widely by..." -- the predicate's subject was the section's
    own heading, dropped during extraction. The heading must travel with
    its body's first chunk so the sentence isn't orphaned."""
    text = (
        "# The Higg Index\n"
        "is a suite of tools used widely by the apparel and footwear sector "
        "to standardise the measurement of value chain sustainability.\n"
    )
    chunker = StructureAwareChunker(chunk_size=512, overlap=64)
    docs = chunker.chunk(text)

    assert len(docs) == 1
    assert docs[0].to_text().startswith("The Higg Index")
    assert "is a suite of tools" in docs[0].to_text()


def test_heading_prepend_only_touches_first_chunk_of_its_section() -> None:
    """A section long enough to split into multiple chunks must only carry
    its heading in the first one -- not repeat it in every chunk."""
    text = "# Repeated Heading Guard\n" + (
        "Sentence padding to force a split into multiple chunks here. " * 20
    )
    chunker = StructureAwareChunker(chunk_size=200, overlap=20)
    docs = chunker.chunk(text)

    assert len(docs) > 1
    assert docs[0].to_text().startswith("Repeated Heading Guard")
    assert all("Repeated Heading Guard" not in doc.to_text() for doc in docs[1:])


def test_small_html_table_is_kept_whole() -> None:
    # A table that already fits within chunk_size is left completely
    # untouched (still raw HTML) — only oversized tables get split and
    # converted to markdown; there's no reason to alter one that fits.
    table = _html_table(n_rows=2)
    text = f"Intro sentence.\n\n{table}\n\nOutro sentence.\n"
    chunker = StructureAwareChunker(chunk_size=2000, overlap=100)
    docs = chunker.chunk(text)

    table_docs = [doc for doc in docs if table in doc.to_text()]
    assert len(table_docs) == 1
