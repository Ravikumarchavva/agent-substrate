"""The monolith's HITL route names the approver itself — not the client's body (row I23, the web half)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any


async def test_i23_the_server_names_the_approver_not_the_client() -> None:
    """The route stamps ``decided_by`` / ``decided_at`` from the authenticated caller. A client that
    puts someone else's name in its body is not believed."""
    from substrate_cloud.monolith.routes.hitl import respond_to_hitl
    from substrate_cloud.monolith.schemas import HITLResponse

    received: dict[str, Any] = {}

    class Registry:
        async def resolve(self, request_id: str, data: dict[str, Any]) -> bool:
            received.update(data)
            return True

    ctx = SimpleNamespace(bridge_registry=Registry())
    claims = SimpleNamespace(sub="the-real-caller")
    body = HITLResponse.model_validate(
        {"action": "approve", "decided_by": "the-cfo", "reason": "ok"}
    )

    await respond_to_hitl("req-1", body, ctx=ctx, user=claims)  # type: ignore[arg-type]

    assert received["decided_by"] == "the-real-caller"
    assert received["decided_at"], "the time of the decision is stamped by the server"
    assert received["action"] == "approve" and received["reason"] == "ok"
