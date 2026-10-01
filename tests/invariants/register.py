"""Builds the invariant register document from the tests themselves.

The audit's single biggest finding was prose that no longer matched code:
docstrings promising at-most-once effects and exactly-once delivery that
nothing tested. So the register is not written by hand — it is read out of the
test functions, and a test asserts the committed document matches. Add a row
and the document is stale until regenerated; change a row's meaning and the
document changes with it.

Regenerate with::

    uv run python -m tests.invariants.register
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

INVARIANTS_DIR = Path(__file__).parent
REPO_ROOT = INVARIANTS_DIR.parents[1]
DOCUMENT = REPO_ROOT / "docs" / "claude_docs" / "architecture" / "invariants.md"


@dataclass(frozen=True)
class Row:
    module: str
    test: str
    summary: str
    pending_reason: str | None

    @property
    def status(self) -> str:
        return "pending" if self.pending_reason else "enforced"


TestNode = ast.FunctionDef | ast.AsyncFunctionDef


def _xfail_reason(node: TestNode) -> str | None:
    """The ``reason=`` of an ``xfail`` decorator, if the test carries one."""
    for decorator in node.decorator_list:
        if not isinstance(decorator, ast.Call):
            continue
        target = decorator.func
        name = ""
        while isinstance(target, ast.Attribute):
            name = target.attr if not name else f"{target.attr}.{name}"
            target = target.value
        if "xfail" not in name:
            continue
        for keyword in decorator.keywords:
            if keyword.arg == "reason" and isinstance(keyword.value, ast.Constant):
                return str(keyword.value.value)
    return None


def _summary(node: TestNode) -> str:
    """The test's first docstring paragraph, as one line.

    The paragraph, not the first line: these docstrings wrap, and taking a
    single line cuts them mid-sentence.
    """
    doc = (ast.get_docstring(node) or "").strip()
    if not doc:
        return node.name.removeprefix("test_").replace("_", " ")
    paragraph = doc.split("\n\n")[0]
    return " ".join(line.strip() for line in paragraph.splitlines() if line.strip())


def collect() -> list[Row]:
    rows: list[Row] = []
    for path in sorted(INVARIANTS_DIR.glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            # Async tests count too — an earlier version of this walk checked
            # only FunctionDef and silently omitted every `async def` row,
            # which is the exact kind of quiet gap the register exists to stop.
            if isinstance(node, TestNode) and node.name.startswith("test_"):
                rows.append(
                    Row(
                        module=path.stem.removeprefix("test_"),
                        test=node.name,
                        summary=_summary(node),
                        pending_reason=_xfail_reason(node),
                    )
                )
    return rows


def render(rows: list[Row]) -> str:
    enforced = sum(1 for row in rows if not row.pending_reason)
    lines = [
        "# The invariant register",
        "",
        "**Generated from `tests/invariants/` — do not edit by hand.**",
        "Regenerate with `uv run python -m tests.invariants.register`.",
        "",
        "Every guarantee this system makes is a test in `tests/invariants/`, not a",
        "sentence in a docstring. A row is *enforced* when its test passes today, and",
        "*pending* when the test exists but the behaviour does not yet — those are",
        "marked `xfail(strict=True)`, so the build fails the moment one starts passing",
        "and the marker has to come off. That is what keeps this document honest.",
        "",
        f"**{enforced} enforced · {len(rows) - enforced} pending · {len(rows)} total**",
        "",
    ]
    for module in sorted({row.module for row in rows}):
        lines.append(f"## {module.replace('_', ' ')}")
        lines.append("")
        for row in [r for r in rows if r.module == module]:
            mark = "✅" if not row.pending_reason else "⏳"
            lines.append(f"- {mark} **{row.summary}**")
            lines.append(f"  `{row.test}`")
            if row.pending_reason:
                lines.append(f"  _Pending — {row.pending_reason}_")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    rows = collect()
    DOCUMENT.parent.mkdir(parents=True, exist_ok=True)
    DOCUMENT.write_text(render(rows))
    enforced = sum(1 for row in rows if not row.pending_reason)
    print(f"wrote {DOCUMENT.relative_to(REPO_ROOT)}: {enforced} enforced, {len(rows) - enforced} pending")


if __name__ == "__main__":
    main()
