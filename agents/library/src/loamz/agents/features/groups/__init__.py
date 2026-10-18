"""The groups context."""

from __future__ import annotations

from loamz.agents.features.feature import Feature, feature

FEATURES: tuple[Feature, ...] = (
    feature("groups.drives", "gateway"),
    feature("groups.feed", "gateway"),
    feature("groups.groups", "gateway"),
)
