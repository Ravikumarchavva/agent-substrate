"""The automation context."""

from __future__ import annotations

from loamz.agents.features.feature import Feature, feature

FEATURES: tuple[Feature, ...] = (
    feature("automation.pipelines", "gateway"),
    feature("automation.schedules", "gateway"),
    feature("automation.triggers", "gateway"),
)
