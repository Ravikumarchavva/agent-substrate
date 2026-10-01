"""Scope — whose data a store handle may touch.

A ``Scope`` is a tenant. A store handle is *bound* to one (``kernel/storage/scoped.py``): every key,
collection, session and namespace it is given is placed inside the tenant, and nothing a caller passes
can name anything outside it. Omitting the scope is not representable — there is no unscoped handle,
only a bound one — so a forgotten argument cannot widen a query.

A scope is built from authenticated input (the run's ``RunScope``, itself stamped by the transport), never
from a model's output or a tool's arguments.
"""

from __future__ import annotations

from pydantic import field_validator

from substrate.kernel.abstractions.core.content import KernelModel


class Scope(KernelModel):
    tenant_id: str

    @field_validator("tenant_id")
    @classmethod
    def _named(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("a scope needs a tenant_id")
        return value

    @classmethod
    def of(cls, run_scope: object) -> Scope:
        """The scope of a run: its tenant, or ``"default"`` for a single-tenant deployment."""
        return cls(tenant_id=getattr(run_scope, "tenant_id", None) or "default")


__all__ = ["Scope"]
