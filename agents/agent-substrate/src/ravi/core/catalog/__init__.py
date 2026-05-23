"""core.catalog — backward-compat re-exports.

Implementation has moved to ``ravi.core.agent_catalog``.
Import from there for new code.
"""

from ravi.core.agent_catalog import AgentCatalog, AgentCatalogRegistry, ResourceSpec, ResourceType
from ravi.core.catalog.registry import CatalogAsset, CatalogSchema
from ravi.core.catalog.lazy_tool import LazyTool

__all__ = [
    "AgentCatalog",
    "AgentCatalogRegistry",
    "CatalogAsset",
    "CatalogSchema",
    "LazyTool",
    "ResourceSpec",
    "ResourceType",
]
