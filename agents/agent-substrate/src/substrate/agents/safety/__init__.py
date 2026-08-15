"""Non-ML input-safety utilities shared by both an L1 guardrail middleware
and L2 concrete classifiers — lives here (not capabilities/safety/) because
import-linter's layer contract only allows upward-to-downward imports
(fabric -> capabilities -> agents -> kernel): agents/middleware/guardrails/
multimodal_safety.py (L1) needs normalize(), and capabilities/safety/ (L2)
needs it too for document-text scanning — L2 importing L1 is allowed, the
reverse is not, so this is the one layer both sides can reach.

Deliberately NOT in kernel: normalize() is concrete logic (a real function
with a real third-party dependency, confusable_homoglyphs), not a
Protocol/dataclass — kernel's own invariants (see
tests/architecture/test_kernel_invariants.py) restrict it to contracts only.
"""

from __future__ import annotations

from substrate.agents.safety.normalize import NormalizedText, normalize

__all__ = ["NormalizedText", "normalize"]
