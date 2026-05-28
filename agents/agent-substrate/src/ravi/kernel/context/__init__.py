"""ravi.kernel.context — ModelContext contract.

Concrete strategies (``SlidingWindowContext``, ``RedisModelContext``,
``HybridContext``, …) live in :mod:`ravi.reasoning.memory.context`.
"""

from __future__ import annotations

from ravi.kernel.context.base_context import ModelContext

__all__ = ["ModelContext"]
