"""The people context."""

from __future__ import annotations

from loamz.server.agents.features.feature import Feature, feature

FEATURES: tuple[Feature, ...] = (
    feature("people.preferences", "gateway"),
    feature("people.notifications", "gateway"),
    feature("people.memory", "gateway"),
    feature("people.usage", "gateway"),
    feature("people.export", "gateway"),
)
