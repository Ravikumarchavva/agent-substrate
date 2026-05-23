"""Backward-compat re-export — implementation moved to ``core.agent_catalog``.

All new code should import from ``ravi.core.agent_catalog`` directly.
This file exists only so existing imports like::

    from ravi.core.catalog.registry import AgentCatalogRegistry, CatalogAsset

keep working while callers are migrated one file at a time.
"""

from __future__ import annotations

from ravi.core.agent_catalog._catalog import (
    AgentCatalog,
    AgentCatalogRegistry,
    _LegacyAssetView as CatalogAsset,  # backward-compat alias
)
from ravi.core.agent_catalog._spec import ResourceSpec, ResourceType

# CatalogSchema is now implicit (schemas live in the FQN namespace).
# Provide a stub for any remaining references.


class CatalogSchema:
    """Backward-compat stub — no longer a real object in the new catalog."""

    def __init__(self, name: str = "", catalog_name: str = "", description: str = "") -> None:
        self.name = name
        self.catalog_name = catalog_name
        self.description = description


__all__ = [
    "AgentCatalog",
    "AgentCatalogRegistry",
    "CatalogAsset",
    "CatalogSchema",
    "ResourceSpec",
    "ResourceType",
]
