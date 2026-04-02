"""Restate runtime backend.

Provides ``RestateRuntime`` — an ``AgentRuntime`` implementation that
executes agent handlers as Restate virtual-object methods, gaining
durable execution, journalling, and transparent HITL suspension via
durable promises.

Requires: ``restate-sdk``.
"""

from __future__ import annotations

from raavan.integrations.runtime.restate.runtime import RestateRuntime

__all__ = ["RestateRuntime"]
