"""The knowledge context."""

from __future__ import annotations

from loamz.agents.features.feature import Feature, feature

FEATURES: tuple[Feature, ...] = (
    feature("knowledge.documents", "gateway"),
    feature("knowledge.enrichment", "gateway"),
    feature("knowledge.rag", "gateway"),
    feature("knowledge.knowledge", "tools"),
)
