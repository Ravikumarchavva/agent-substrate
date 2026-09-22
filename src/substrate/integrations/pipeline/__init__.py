"""substrate.integrations.pipeline — declarative pipeline execution engine."""

from __future__ import annotations

from substrate.integrations.pipeline.data_ref import (
    DataRef,
    DataRefStore,
    DataRefArtifactStore,
)
from substrate.integrations.pipeline.engine import (
    PipelineDef,
    PipelineEngine,
    PipelineResult,
    PipelineStep,
)
from substrate.integrations.pipeline.store import PipelineStore

__all__ = [
    "DataRef",
    "DataRefStore",
    "DataRefArtifactStore",
    "PipelineDef",
    "PipelineEngine",
    "PipelineResult",
    "PipelineStep",
    "PipelineStore",
]
