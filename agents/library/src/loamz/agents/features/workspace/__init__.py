"""The workspace context."""

from __future__ import annotations

from loamz.agents.features.feature import Feature, feature

FEATURES: tuple[Feature, ...] = (
    feature("workspace.artifacts", "gateway"),
    feature("workspace.files", "gateway"),
    feature("workspace.workspace", "gateway"),
)
