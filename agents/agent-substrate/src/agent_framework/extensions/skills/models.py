"""
Agent Skills - Models & Data Structures

Implements the Agent Skills open standard (https://agentskills.io).
A Skill is a folder containing SKILL.md (YAML frontmatter + Markdown body)
plus optional scripts/, references/, and assets/ directories.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# SKILL.md frontmatter validation rules (per spec)
# ---------------------------------------------------------------------------

_NAME_RE = re.compile(r'^[a-z0-9][a-z0-9\-]*[a-z0-9]$|^[a-z0-9]$')


def _validate_name(name: str) -> None:
    if not name or len(name) > 64:
        raise ValueError(f"Skill name must be 1-64 characters, got: {name!r}")
    if not _NAME_RE.match(name):
        raise ValueError(
            f"Skill name must be lowercase alphanumeric + hyphens, "
            f"not start/end with hyphen, no consecutive hyphens. Got: {name!r}"
        )
    if '--' in name:
        raise ValueError(f"Skill name must not contain consecutive hyphens: {name!r}")


def _validate_description(desc: str) -> None:
    if not desc or len(desc) > 1024:
        raise ValueError(
            f"Skill description must be 1-1024 characters, got length: {len(desc)}"
        )


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class SkillMetadata:
    """
    Lightweight metadata loaded at agent startup.
    Only name + description are loaded initially (~50-100 tokens each).
    This is injected into the system prompt so the model knows skills exist.
    """
    name: str
    description: str
    path: Path                          # absolute path to the skill directory
    skill_md_path: Path                 # absolute path to SKILL.md
    license: Optional[str] = None
    compatibility: Optional[str] = None
    allowed_tools: List[str] = field(default_factory=list)
    version: str = "1.0"
    metadata: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_name(self.name)
        _validate_description(self.description)


@dataclass
class Skill:
    """
    Fully loaded skill – activated when the model decides to use it.
    Contains the full SKILL.md body + paths to auxiliary files.
    """
    metadata: SkillMetadata
    body: str                           # Full Markdown body (instructions)
    scripts: List[Path] = field(default_factory=list)
    references: List[Path] = field(default_factory=list)
    assets: List[Path] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.metadata.name

    @property
    def description(self) -> str:
        return self.metadata.description

    @property
    def path(self) -> Path:
        return self.metadata.path

    def list_scripts(self) -> List[str]:
        """Return script names relative to the skill root."""
        return [s.name for s in self.scripts]

    def list_references(self) -> List[str]:
        """Return reference file names relative to the skill root."""
        return [r.name for r in self.references]

    def read_reference(self, filename: str) -> Optional[str]:
        """Read a reference file by name; returns None if not found."""
        for ref in self.references:
            if ref.name == filename:
                return ref.read_text(encoding="utf-8")
        return None

    def read_script(self, filename: str) -> Optional[str]:
        """Read a script file by name; returns None if not found."""
        for s in self.scripts:
            if s.name == filename:
                return s.read_text(encoding="utf-8")
        return None

    def to_context_block(self) -> str:
        """
        Return the full skill content formatted for injection into context.
        Includes instructions + list of available scripts and references.
        """
        lines = [
            f"# Skill: {self.name}",
            f"",
            self.body.strip(),
        ]
        if self.scripts:
            lines += ["", "## Available Scripts", ""]
            for s in self.scripts:
                lines.append(f"- `scripts/{s.name}`")
        if self.references:
            lines += ["", "## Reference Files", ""]
            for r in self.references:
                lines.append(f"- `references/{r.name}`")
        return "\n".join(lines)
