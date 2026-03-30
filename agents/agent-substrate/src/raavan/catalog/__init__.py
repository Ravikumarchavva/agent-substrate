"""Catalog — unified capability system for tools, skills, connectors, and pipelines.

Directory layout::

    catalog/
    ├── tools/        ← BaseTool implementations (capability_search, web_surfer, …)
    ├── skills/       ← SKILL.md prompt-skill packages (debugging, code_review, …)
    ├── connectors/   ← External service connectors (email, postgres_query, …)
    ├── _chain_runtime.py
    ├── _data_ref.py
    ├── _pipeline.py
    ├── _scanner.py
    ├── _temporal/
    └── _triggers/
"""

from __future__ import annotations

from raavan.catalog._chain_runtime import ChainRuntime
from raavan.catalog._data_ref import DataRef, DataRefStore
from raavan.catalog._pipeline import (
    PipelineDef,
    PipelineEngine,
    PipelineStore,
)
from raavan.catalog._scanner import CatalogPackage, CatalogScanner
from raavan.catalog._skill_manager import SkillManager
from raavan.catalog._skill_loader import SkillLoader
from raavan.catalog._skill_models import Skill, SkillMetadata

__all__ = [
    "CatalogPackage",
    "CatalogScanner",
    "ChainRuntime",
    "DataRef",
    "DataRefStore",
    "PipelineDef",
    "PipelineEngine",
    "PipelineStore",
    "Skill",
    "SkillLoader",
    "SkillManager",
    "SkillMetadata",
]
