"""ravi.kernel.context — Context compaction contracts.

The canonical abstraction is :class:`CompactionStrategy`. ``ModelContext``
is a backward-compat alias. Concrete strategies live in
:mod:`ravi.reasoning.memory.context`.
"""

from __future__ import annotations

from ravi.kernel.context.compaction import CompactionStrategy, Trigger
from ravi.kernel.context.base_context import ModelContext

__all__ = ["CompactionStrategy", "Trigger", "ModelContext"]
