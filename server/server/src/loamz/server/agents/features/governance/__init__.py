"""The governance context."""

from __future__ import annotations

from loamz.server.agents.features.feature import Feature, feature

FEATURES: tuple[Feature, ...] = (
    feature("governance.admin", "admin"),
    feature("governance.connector_tokens", "admin", "gateway"),
    feature("governance.entitlements", "gateway"),
    feature("governance.gdpr", "admin"),
    feature("governance.health", "gateway"),
    feature("governance.rate_limit", "gateway"),
    feature("governance.workspace_oauth", "gateway"),
)
