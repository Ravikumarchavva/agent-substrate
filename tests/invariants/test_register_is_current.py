"""The register document is generated, so it cannot be allowed to go stale.

This is the mechanism that answers the audit's central finding — prose drifting
away from code — for the register itself. Add, remove or reword a row and this
fails until the document is regenerated in the same commit.
"""

from __future__ import annotations

import pytest

from tests.invariants.register import DOCUMENT, INVARIANTS_DIR, REPO_ROOT, collect, render


def test_the_register_document_matches_the_tests() -> None:
    expected = render(collect())
    assert DOCUMENT.exists(), (
        f"{DOCUMENT.relative_to(REPO_ROOT)} is missing — generate it with "
        "`uv run python -m tests.invariants.register`"
    )
    assert DOCUMENT.read_text() == expected, (
        f"{DOCUMENT.relative_to(REPO_ROOT)} is out of date. Regenerate it with "
        "`uv run python -m tests.invariants.register` and commit the result."
    )


def test_the_register_lists_every_invariant_test(request: pytest.FixtureRequest) -> None:
    """Guards the collector against the gap it already had once.

    The register is built by parsing these files, so a shape it fails to
    recognise disappears from the document without a word. Comparing against
    what pytest actually collected is the only way to notice.
    """
    collected = {
        # Strip a parametrisation suffix: pytest reports `test_x[in_memory]`,
        # while the register lists the one function `test_x`.
        item.name.split("[")[0]
        for item in request.session.items
        if item.path.parent == INVARIANTS_DIR and item.path.name.startswith("test_")
    }
    listed = {row.test for row in collect()}
    missing = sorted(collected - listed)
    assert not missing, (
        "these tests ran but do not appear in the register — the collector in "
        f"register.py does not recognise their shape: {missing}"
    )
