"""Agent Lifecycle Management.

Provides primitives for dynamic agent instantiation (The Wukong Spawner)
and suspension (HITL Continuations).
"""

from .spawner import Spawner
from .continuation import Continuation

__all__ = [
    "Spawner",
    "Continuation",
]
