"""The conversations context."""

from __future__ import annotations

from loamz.agents.features.feature import Feature, feature

FEATURES: tuple[Feature, ...] = (
    feature("conversations.audio", "gateway"),
    feature("conversations.branches", "gateway"),
    feature("conversations.chat", "gateway"),
    feature("conversations.feedback", "gateway"),
    feature("conversations.mcp_apps", "gateway"),
    feature("conversations.shares", "gateway"),
    feature("conversations.tasks", "gateway"),
    feature("conversations.threads", "gateway"),
)
