"""Architecture invariants for the agent-skills system.

Mirrors ``test_kernel_invariants.py``'s approach: cheap, real checks that
catch what CI would otherwise silently pass. ``SkillLoader`` is intentionally
forgiving at runtime — a malformed or duplicate-named skill just gets logged
and dropped, so a typo in one skill's frontmatter never takes down the
server — but that same leniency means CI must check explicitly, or a broken
skill merges unnoticed and is only ever discovered by someone wondering why
it doesn't show up.

See ``docs/claude_docs/architecture/prompt-and-skills.md`` for why a skill's
own SKILL.md body has a line-count ceiling: past it, the model is re-reading
narrative that belongs in ``references/`` (read on demand) rather than in
the body every activation returns.
"""

from __future__ import annotations

import logging
from pathlib import Path

from substrate.capabilities.tools.skills._loader import SkillLoader

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILLS_DIR = REPO_ROOT / "src" / "substrate" / "capabilities" / "tools" / "skills"

# excel_report is the most complex skill by design (chart-quality guard
# functions) and is deliberately split across SKILL.md + references/ to fit
# under this — see its Step 2. Raise this only alongside an equivalent split
# for whatever skill would otherwise exceed it.
MAX_SKILL_BODY_LINES = 150


def _real_skill_dirs() -> list[Path]:
    return [
        p for p in SKILLS_DIR.iterdir() if p.is_dir() and (p / "SKILL.md").is_file()
    ]


def test_all_real_skills_discover_without_warnings(caplog) -> None:
    """A skill with malformed frontmatter or a missing description is
    silently dropped by SkillLoader (by design — see module docstring), so
    this test is the only thing that actually catches it before merge."""
    with caplog.at_level(logging.WARNING, logger="substrate"):
        loader = SkillLoader(skill_dirs=[SKILLS_DIR])
        skills = loader.discover_all()

    real_dirs = _real_skill_dirs()
    discovered_names = {s.name for s in skills}
    assert len(skills) == len(real_dirs), (
        f"Discovered {len(skills)} skills but found {len(real_dirs)} skill "
        f"directories on disk — one or more were silently dropped (check "
        f"caplog for the warning): {sorted(p.name for p in real_dirs)} vs "
        f"{sorted(discovered_names)}"
    )
    warnings = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
    assert not warnings, f"Skill discovery logged warnings: {warnings}"


def test_skill_names_are_unique() -> None:
    loader = SkillLoader(skill_dirs=[SKILLS_DIR])
    skills = loader.discover_all()
    names = [s.name for s in skills]
    assert len(names) == len(set(names)), (
        f"Duplicate skill names found: {[n for n in names if names.count(n) > 1]}"
    )


def test_skill_bodies_are_under_the_line_ceiling() -> None:
    """A SKILL.md body over the ceiling should move detail into
    references/ (read on demand) rather than grow the text every
    activation returns in full — see excel_report/SKILL.md for the pattern."""
    loader = SkillLoader(skill_dirs=[SKILLS_DIR])
    metadatas = loader.discover_all()  # populates the index load_skill needs
    packages = [loader.load_skill(m.name) for m in metadatas]
    oversized = {
        pkg.name: len(pkg.body.splitlines())
        for pkg in packages
        if pkg is not None and len(pkg.body.splitlines()) > MAX_SKILL_BODY_LINES
    }
    assert not oversized, (
        f"Skill body/ies over the {MAX_SKILL_BODY_LINES}-line ceiling: "
        f"{oversized} — move detail into that skill's references/ instead."
    )
