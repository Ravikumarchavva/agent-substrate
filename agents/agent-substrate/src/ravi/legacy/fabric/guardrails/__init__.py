"""ravi.fabric.guardrails — guardrail contracts + trust/provenance types.

Base ABC, context, result, type enum, and spec. Concrete guardrails
(``PIIDetectionGuardrail``, ``ContentFilterGuardrail``, …)
and the parallel runner live in :mod:`ravi.guardrails`.
"""

from ravi.fabric.guardrails.base_guardrail import (
    BaseGuardrail,
    GuardrailContext,
    GuardrailResult,
    GuardrailType,
)
from ravi.fabric.guardrails.spec import GuardrailSpec
from ravi.fabric.guardrails._trust_contracts import (
    DelegationProof,
    PrincipalTrustContext,
    ProvenanceChain,
    ProvenanceLink,
    TrustGraph,
    TrustScore,
)

__all__ = [
    "BaseGuardrail",
    "GuardrailContext",
    "GuardrailResult",
    "GuardrailType",
    "GuardrailSpec",
    "DelegationProof",
    "PrincipalTrustContext",
    "ProvenanceChain",
    "ProvenanceLink",
    "TrustGraph",
    "TrustScore",
]
