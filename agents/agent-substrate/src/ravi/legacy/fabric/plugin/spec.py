"""PluginSpec — metadata for a single registered extension class."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Type


@dataclass(frozen=True)
class PluginSpec:
    """Frozen record of a registered extension class.

    Attributes:
        category: Plugin category (``"agent"``, ``"guardrail"``, …).
        name: Lookup name within the category, unique per category.
        cls: The registered class. Must be a subclass of the category's base.
    """

    category: str
    name: str
    cls: Type[object]

    @property
    def fqn(self) -> str:
        """Fully-qualified plugin key: ``"{category}:{name}"``."""
        return f"{self.category}:{self.name}"
