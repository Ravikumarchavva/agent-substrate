"""Catalog — unified capability system for tools, skills, connectors, and pipelines.

Directory layout::

    catalog/
    ├── tools/        ← BaseTool implementations (capability_search, web_surfer, …)
    ├── skills/       ← SKILL.md prompt-skill packages (debugging, code_review, …)
    ├── connectors/   ← External service connectors (email, postgres_query, …)
    ├── triggers/     ← Trigger monitors (scheduled, webhooks, events)
    └── internal/     ← Internal catalog implementations (scanners, managers, runtime)
"""

from __future__ import annotations

from ravi.capabilities.internal.chain_runtime import ChainRuntime
from ravi.capabilities.internal.data_ref import DataRef, DataRefStore
from ravi.capabilities.internal.pipeline import (
    PipelineDef,
    PipelineEngine,
    PipelineStore,
)
from ravi.capabilities.internal.scanner import CatalogPackage, CatalogScanner
from ravi.capabilities.internal.skill_manager import SkillManager
from ravi.capabilities.internal.skill_loader import SkillLoader
from ravi.capabilities.internal.skill_models import Skill, SkillMetadata

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
