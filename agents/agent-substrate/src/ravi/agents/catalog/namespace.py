from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class Namespace:
    """Hierarchical identifier for a capability — catalog.schema.name.

    Example: ``core.finance.sql_reader``
    """

    catalog: str
    schema: str
    name: str

    def __str__(self) -> str:
        return f"{self.catalog}.{self.schema}.{self.name}"

    @classmethod
    def from_string(cls, fqn: str) -> Namespace:
        parts = fqn.split(".")
        if len(parts) != 3:
            raise ValueError(
                f"Namespace must be 'catalog.schema.name', got: {fqn!r}"
            )
        return cls(catalog=parts[0], schema=parts[1], name=parts[2])


@dataclass(frozen=True)
class Capability:
    """A governed capability an agent can discover and bind.

    A capability can be an executable tool, a spawnable agent blueprint,
    or a data resource.
    """

    namespace: Namespace
    capability_type: Literal["tool", "blueprint", "data"]
    description: str
    schema_definition: dict[str, Any]
    metadata: dict[str, str] | None = None
