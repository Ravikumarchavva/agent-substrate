"""Unity Catalog-style capability registry for dynamic agent discovery.

This module provides the governance and discovery layer for agent blueprints,
tools, and data access. It enforces hierarchical namespace rules and ACLs
to prevent context bloat and ensure secure capability distribution.
"""

from .namespace import Namespace, Capability
from .registry import CapabilityRegistry

__all__ = [
    "Namespace",
    "Capability",
    "CapabilityRegistry",
]
