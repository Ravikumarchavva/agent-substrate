"""``split`` — a document's markdown into sections a model can read whole."""

from __future__ import annotations

from substrate.documents.split import HARD_MAX_TOKENS, MIN_TOKENS, split, tokens

BODY = " ".join(["word"] * 900)  # ~1,200 tokens


def test_it_splits_at_the_shallowest_level_with_two_headings_and_names_the_path():
    md = f"<!-- page 1 -->\n\n# Report\n\n{BODY}\n\n## Revenue\n\n{BODY}\n\n<!-- page 2 -->\n\n## Risks\n\n{BODY}\n\n### Supply\n\n{BODY}"
    sections = split(md, title="Report")
    assert [s.title for s in sections] == ["Report", "Revenue", "Risks"]  # the preamble is big enough to stand alone
    risks = sections[-1]
    assert risks.heading_path[-1] == "Risks" and risks.first_page == 2 and "### Supply" in risks.markdown


def test_a_lone_title_is_not_a_split():
    assert len(split(f"# Only title\n\n{BODY}\n\n{BODY}")) == 1


def test_a_tiny_section_is_merged_into_its_neighbour():
    md = f"## A\n\n{BODY}\n\n## B\n\nshort\n\n## C\n\n{BODY}"
    assert [s.title for s in split(md)] == ["A", "C"] or len(split(md)) == 2
    assert all(s.tokens >= MIN_TOKENS or len(split(md)) == 1 for s in split(md))


def test_an_oversized_section_is_split_at_its_next_heading_level_then_pages_then_paragraphs():
    big = " ".join(["word"] * 7000)  # ~9,300 tokens: over the cap on its own
    by_heading = split(f"## One\n\n### a\n\n{big}\n\n### b\n\n{big}")
    assert len(by_heading) >= 2 and all(s.tokens <= HARD_MAX_TOKENS for s in by_heading)
    by_page = split(f"<!-- page 1 -->\n\n## One\n\n{big}\n\n<!-- page 2 -->\n\n{big}\n\n## Two\n\n{BODY}")
    assert all(s.tokens <= HARD_MAX_TOKENS for s in by_page) and {s.first_page for s in by_page} >= {1, 2}
    paragraphs = "\n\n".join(" ".join(["word"] * 600) for _ in range(30))
    assert all(s.tokens <= HARD_MAX_TOKENS for s in split(f"## One\n\n{paragraphs}\n\n## Two\n\n{BODY}"))


def test_without_headings_whole_pages_are_grouped_and_named_by_their_pages():
    pages = "\n\n".join(f"<!-- page {i} -->\n\nSlide {i} " + " ".join(["w"] * 3000) for i in range(1, 8))
    sections = split(pages)
    assert [s.title for s in sections][0].startswith("Pages 1") and sections[-1].last_page == 7
    assert [s.first_page for s in sections] == sorted(s.first_page for s in sections)
    assert "".join(s.markdown for s in sections).count("Slide") == 7  # nothing lost


def test_a_hash_inside_a_code_fence_is_not_a_heading():
    md = f"## Real\n\n{BODY}\n\n```\n# comment\n## also a comment\n```\n\n## Next\n\n{BODY}"
    assert [s.title for s in split(md)] == ["Real", "Next"]


def test_empty_text_has_no_sections_and_tokens_over_estimates():
    assert split("  \n\n") == [] and tokens("a" * 300) == 101
