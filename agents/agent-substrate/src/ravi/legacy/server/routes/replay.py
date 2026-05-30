"""Admin routes for the ReplayGate — Section 14.

Exposes the operator-facing replay admission control gate so administrators
can:

- Submit a replay request and receive an admission decision.
- Query a prior admission by idempotency key.
- Add deny rules that block replays for specific envelopes or correlations.
- Remove deny rules that are no longer needed.

All endpoints require ``platform_admin`` or ``tenant_admin`` role.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(prefix="/admin/replay", tags=["admin", "replay"])


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


class ReplayRequestBody(BaseModel):
    envelope_id: str
    correlation_id: str
    requested_by: str
    reason: str
    idempotency_key: Optional[str] = None


class DenyRuleBody(BaseModel):
    reason: str
    created_by: str
    envelope_id: Optional[str] = None
    correlation_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _gate(request: Request):
    gate = getattr(request.app.state, "replay_gate", None)
    if gate is None:
        raise HTTPException(status_code=503, detail="ReplayGate not initialised")
    return gate


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/admit", summary="Admit or deny a replay request")
async def admit_replay(body: ReplayRequestBody, request: Request) -> Dict[str, Any]:
    """Submit a replay request; idempotent on idempotency_key."""
    from ravi.adapters.observability import ReplayRequest

    gate = _gate(request)
    kwargs: dict[str, Any] = dict(
        envelope_id=body.envelope_id,
        correlation_id=body.correlation_id,
        requested_by=body.requested_by,
        reason=body.reason,
    )
    if body.idempotency_key is not None:
        kwargs["idempotency_key"] = body.idempotency_key

    req = ReplayRequest(**kwargs)
    admission = await gate.admit(req)
    return {
        "idempotency_key": admission.idempotency_key,
        "envelope_id": admission.envelope_id,
        "correlation_id": admission.correlation_id,
        "allowed": admission.allowed,
        "status": admission.status.name,
        "reason": admission.reason,
        "decided_at": admission.decided_at.isoformat(),
        "replay_token": admission.replay_token,
    }


@router.get(
    "/admission/{idempotency_key}",
    summary="Query a prior replay admission decision",
)
async def get_admission(idempotency_key: str, request: Request) -> Dict[str, Any]:
    """Return the admission decision for ``idempotency_key``, or 404."""
    gate = _gate(request)
    admission = await gate.admission_for(idempotency_key)
    if admission is None:
        raise HTTPException(status_code=404, detail="Admission not found")
    return {
        "idempotency_key": admission.idempotency_key,
        "envelope_id": admission.envelope_id,
        "correlation_id": admission.correlation_id,
        "allowed": admission.allowed,
        "status": admission.status.name,
        "reason": admission.reason,
        "decided_at": admission.decided_at.isoformat(),
        "replay_token": admission.replay_token,
    }


@router.post("/deny", summary="Add an operator deny rule")
async def add_deny_rule(body: DenyRuleBody, request: Request) -> Dict[str, Any]:
    """Add a rule that permanently denies replay for matching envelopes."""
    from ravi.adapters.observability import ReplayDenyRule

    if body.envelope_id is None and body.correlation_id is None:
        raise HTTPException(
            status_code=422,
            detail="Deny rule must target an envelope_id or correlation_id",
        )
    gate = _gate(request)
    rule = ReplayDenyRule(
        reason=body.reason,
        created_by=body.created_by,
        envelope_id=body.envelope_id,
        correlation_id=body.correlation_id,
    )
    await gate.deny(rule)
    return {
        "rule_id": rule.rule_id,
        "reason": rule.reason,
        "created_by": rule.created_by,
        "envelope_id": rule.envelope_id,
        "correlation_id": rule.correlation_id,
        "created_at": rule.created_at.isoformat(),
    }


@router.delete("/deny/{rule_id}", summary="Remove a deny rule")
async def clear_deny_rule(rule_id: str, request: Request) -> Dict[str, Any]:
    """Remove the deny rule with ``rule_id``. Returns 404 when not found."""
    gate = _gate(request)
    removed = await gate.clear_denial(rule_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Deny rule not found")
    return {"removed": rule_id}
