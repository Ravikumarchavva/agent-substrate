"""Input-safety detectors: concrete text/image classifiers and (added
incrementally per the implementation plan) the document scanner.

Concrete implementations of the ``TextSafetyClassifier``/``ImageSafetyClassifier``
Protocols from ``kernel/agent/safety.py`` live here — this module is L2, so it
may hold real model/inference code that kernel and agents/middleware cannot.

The text normalizer moved to ``agents/safety/normalize.py`` — see that
module's docstring for why (an L1 guardrail middleware needs it too, and
import-linter's layer contract only allows L2 to import L1, not the
reverse). Re-exported here for convenience since callers reaching into
``capabilities.safety`` for classifiers commonly want normalization too.
"""

from __future__ import annotations

from substrate.agents.safety.normalize import NormalizedText, normalize

__all__ = ["NormalizedText", "normalize"]
