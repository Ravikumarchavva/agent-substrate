"""The directory context."""

from __future__ import annotations

from loamz.agents.features.feature import Feature, feature

FEATURES: tuple[Feature, ...] = (feature("directory.agents", "gateway"),)
