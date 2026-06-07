from __future__ import annotations

from pathlib import Path
from ravi.capabilities.skills._loader import SkillLoader


def test_skill_loader_parsing(tmp_path):
    skill_dir = tmp_path / "mock_skill"
    skill_dir.mkdir()
    
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text("""---
name: mock-skill
description: A mock skill for testing skill loader.
license: MIT
version: "1.2"
allowed-tools: echo risky
tags:
  - test
  - mock
---

# Mock Skill
This is the mock skill instructions body.
""")

    loader = SkillLoader(skill_dirs=[tmp_path])
    metadatas = loader.discover_all()
    
    assert len(metadatas) == 1
    meta = metadatas[0]
    assert meta.name == "mock-skill"
    assert meta.description == "A mock skill for testing skill loader."
    assert meta.license == "MIT"
    assert meta.version == "1.2"
    assert meta.allowed_tools == ["echo", "risky"]
    assert meta.tags == ["test", "mock"]

    # Verify lazy load full activation
    skill = loader.load_skill("mock-skill")
    assert skill is not None
    assert skill.metadata is meta
    assert "# Mock Skill" in skill.body
