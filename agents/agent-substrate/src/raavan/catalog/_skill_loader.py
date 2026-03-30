"""
Agent Skills - Skill Loader

Scans filesystem directories for SKILL.md files, parses YAML frontmatter,
and provides lazy full-load on skill activation.

SKILL.md format expected:
    ---
    name: skill-name
    description: Short description of what this skill does.
    license: MIT              # optional
    version: "1.0"            # optional
    compatibility: ">=3.10"   # optional
    allowed-tools: tool1 tool2  # optional space-delimited
    metadata:                 # optional key/value
      key: value
    ---

    # Skill Body
    Markdown instructions...
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from raavan.catalog._skill_models import Skill, SkillMetadata

yaml: Any = None  # optional dependency; assigned below if available
try:
    import yaml

    _YAML_AVAILABLE = True
except ImportError:
    _YAML_AVAILABLE = False

logger = logging.getLogger(__name__)


def _default_skill_dirs() -> List[Path]:
    """Return built-in catalog/skills plus the user's local skill directory."""
    package_root = Path(__file__).resolve().parents[2]
    return [
        package_root / "catalog" / "skills",
        Path("~/.claude/skills").expanduser(),
    ]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_frontmatter(raw: str) -> tuple[dict, str]:
    """
    Split a SKILL.md file into (frontmatter_dict, body_markdown).
    Returns ({}, raw) if no frontmatter found.
    """
    if not raw.startswith("---"):
        return {}, raw

    # Find closing ---
    rest = raw[3:]
    end = rest.find("\n---")
    if end == -1:
        return {}, raw

    fm_text = rest[:end].strip()
    body = rest[end + 4 :].strip()  # skip \n---

    if not _YAML_AVAILABLE:
        logger.warning(
            "PyYAML is not installed – cannot parse SKILL.md frontmatter. "
            "Install it with: pip install pyyaml"
        )
        return {}, body

    try:
        fm = yaml.safe_load(fm_text) or {}
    except yaml.YAMLError as exc:
        logger.warning("Failed to parse SKILL.md YAML frontmatter: %s", exc)
        fm = {}

    return fm, body


def _list_dir_files(directory: Path) -> List[Path]:
    """Return sorted list of files directly inside a directory."""
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.iterdir() if p.is_file())


# ---------------------------------------------------------------------------
# Public SkillLoader
# ---------------------------------------------------------------------------


class SkillLoader:
    """
    Discovers and loads Agent Skills from filesystem directories.

    Usage:
        loader = SkillLoader()  # package skills + ~/.claude/skills
        metadatas = loader.discover_all()       # lightweight metadata only
        skill = loader.load_skill("skill-name") # full content on activation
    """

    def __init__(
        self,
        skill_dirs: Optional[List[str | Path]] = None,
    ) -> None:
        self._dirs: List[Path] = []
        configured_dirs = _default_skill_dirs() if skill_dirs is None else skill_dirs
        for d in configured_dirs:
            p = Path(d).expanduser().resolve()
            if p.is_dir():
                self._dirs.append(p)
            else:
                logger.debug("Skills directory not found (skipping): %s", p)

        # Cache: name -> SkillMetadata (populated by discover_all)
        self._metadata: Dict[str, SkillMetadata] = {}
        # Cache: name -> Skill (populated lazily by load_skill)
        self._loaded: Dict[str, Skill] = {}

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def discover_all(self) -> List[SkillMetadata]:
        """
        Scan all configured directories for skill folders containing SKILL.md.
        Returns a list of SkillMetadata objects (no full body loaded).
        Only the first occurrence of a skill name is kept (earlier dirs win).
        """
        found: Dict[str, SkillMetadata] = {}
        for base_dir in self._dirs:
            for skill_dir in sorted(base_dir.iterdir()):
                if not skill_dir.is_dir():
                    continue
                skill_md = skill_dir / "SKILL.md"
                if not skill_md.is_file():
                    continue
                meta = self._load_metadata(skill_dir, skill_md)
                if meta is None:
                    continue
                if meta.name in found:
                    logger.debug(
                        "Skill %r already discovered – skipping %s",
                        meta.name,
                        skill_dir,
                    )
                else:
                    found[meta.name] = meta
                    logger.debug("Discovered skill: %r at %s", meta.name, skill_dir)

        self._metadata = found
        return list(found.values())

    def _load_metadata(
        self, skill_dir: Path, skill_md: Path
    ) -> Optional[SkillMetadata]:
        """Parse SKILL.md and return SkillMetadata; returns None on error."""
        try:
            raw = skill_md.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Cannot read %s: %s", skill_md, exc)
            return None

        fm, _body = _parse_frontmatter(raw)

        # Extract required fields
        name = fm.get("name") or skill_dir.name
        description = fm.get("description") or ""

        if not description:
            logger.warning(
                "Skill at %s has no 'description' in frontmatter – skipping", skill_dir
            )
            return None

        # Parse allowed-tools (space-delimited string or list)
        allowed_tools_raw = fm.get("allowed-tools") or fm.get("allowed_tools") or ""
        if isinstance(allowed_tools_raw, list):
            allowed_tools = [str(t) for t in allowed_tools_raw]
        else:
            allowed_tools = str(allowed_tools_raw).split() if allowed_tools_raw else []

        # Parse catalog metadata (tags, aliases, category)
        tags_raw = fm.get("tags") or []
        if isinstance(tags_raw, list):
            tags = [str(t).lower() for t in tags_raw]
        else:
            tags = str(tags_raw).split() if tags_raw else []

        aliases_raw = fm.get("aliases") or []
        if isinstance(aliases_raw, list):
            aliases = [str(a).lower() for a in aliases_raw]
        else:
            aliases = str(aliases_raw).split() if aliases_raw else []

        category = str(fm.get("category") or "")

        try:
            return SkillMetadata(
                name=str(name),
                description=str(description),
                path=skill_dir,
                skill_md_path=skill_md,
                license=fm.get("license"),
                version=str(fm.get("version", "1.0")),
                compatibility=fm.get("compatibility"),
                allowed_tools=allowed_tools,
                metadata={k: str(v) for k, v in (fm.get("metadata") or {}).items()},
                category=category,
                tags=tags,
                aliases=aliases,
            )
        except ValueError as exc:
            logger.warning("Invalid skill at %s: %s", skill_dir, exc)
            return None

    # ------------------------------------------------------------------
    # Activation (lazy full-load)
    # ------------------------------------------------------------------

    def get_metadata(self, name: str) -> Optional[SkillMetadata]:
        """Return metadata for a skill by name (must have run discover_all first)."""
        return self._metadata.get(name)

    def all_metadata(self) -> List[SkillMetadata]:
        """Return all discovered metadata objects."""
        return list(self._metadata.values())

    def load_skill(self, name: str) -> Optional[Skill]:
        """
        Fully load a skill by name (activates it).
        Returns cached Skill if already activated.
        """
        if name in self._loaded:
            return self._loaded[name]

        meta = self._metadata.get(name)
        if meta is None:
            logger.warning("load_skill: skill %r not found in index", name)
            return None

        return self._load_full(meta)

    def load_skill_by_path(self, skill_dir: Path) -> Optional[Skill]:
        """Directly load a skill from its directory path (bypasses index)."""
        skill_dir = Path(skill_dir).expanduser().resolve()
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.is_file():
            logger.warning("No SKILL.md found at %s", skill_dir)
            return None

        meta = self._load_metadata(skill_dir, skill_md)
        if meta is None:
            return None

        self._metadata[meta.name] = meta
        return self._load_full(meta)

    def _load_full(self, meta: SkillMetadata) -> Skill:
        """Parse SKILL.md body and enumerate auxiliary files."""
        try:
            raw = meta.skill_md_path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.error("Cannot read skill body %s: %s", meta.skill_md_path, exc)
            raw = ""

        _fm, body = _parse_frontmatter(raw)

        skill = Skill(
            metadata=meta,
            body=body,
            scripts=_list_dir_files(meta.path / "scripts"),
            references=_list_dir_files(meta.path / "references"),
            assets=_list_dir_files(meta.path / "assets"),
        )

        self._loaded[meta.name] = skill
        logger.debug(
            "Activated skill %r (%d scripts, %d refs, %d assets)",
            meta.name,
            len(skill.scripts),
            len(skill.references),
            len(skill.assets),
        )
        return skill
