"""capabilities — the authoritative home for everything agents can use.

Directory layout::

    capabilities/
    ├── tools/          ← all tool implementations + skills + connectors + discovery
    │   ├── skills/     ← SKILL.md prompt-skill packages
    │   ├── connectors/ ← stateful service connectors
    │   ├── discovery.py ← CapabilityDiscovery (startup filesystem scanner)
    │   └── web/, files/, ai/, compute/, utils/, communication/, …
    ├── knowledge/    ← RAG pipeline, chunkers, loaders, reranker
    ├── pipeline/     ← declarative pipeline execution engine
    ├── memory/       ← ShortTermMemory + LongTermMemory implementations
    ├── history/      ← HistoryProvider implementations
    ├── vector/       ← VectorStore implementations
    ├── graph/        ← GraphStore implementations
    └── triggers/     ← trigger monitors (scheduled, webhooks, events)
"""

from __future__ import annotations

from ravi.capabilities.pipeline.chain import ChainRuntime
from ravi.capabilities.pipeline.data_ref import DataRef, DataRefStore
from ravi.capabilities.pipeline.engine import (
    PipelineDef,
    PipelineEngine,
    PipelineResult,
)
from ravi.capabilities.pipeline.store import PipelineStore
from ravi.capabilities.tools.discovery import CatalogPackage, CapabilityDiscovery
from ravi.capabilities.tools.skills._manager import SkillManager
from ravi.capabilities.tools.skills._loader import SkillLoader
from ravi.capabilities.tools.skills._models import SkillPackage, SkillMetadata

# Backward-compat alias
CatalogScanner = CapabilityDiscovery

__all__ = [
    "CatalogPackage",
    "CatalogScanner",
    "CapabilityDiscovery",
    "ChainRuntime",
    "DataRef",
    "DataRefStore",
    "PipelineDef",
    "PipelineEngine",
    "PipelineResult",
    "PipelineStore",
    "SkillLoader",
    "SkillManager",
    "SkillMetadata",
    "SkillPackage",
]
