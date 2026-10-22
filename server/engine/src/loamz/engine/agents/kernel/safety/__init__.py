"""loamz.engine.agents.kernel.safety — Safety classifier contracts and the text normalisation they share."""

from __future__ import annotations

from loamz.engine.agents.kernel.safety.normalize import NormalizedText, normalize
from loamz.engine.agents.kernel.safety.protocols import (
    ImageSafetyClassifier,
    SafetyVerdict,
    Severity,
    TextSafetyClassifier,
    max_severity,
)

__all__ = [
    "ImageSafetyClassifier",
    "NormalizedText",
    "SafetyVerdict",
    "Severity",
    "TextSafetyClassifier",
    "max_severity",
    "normalize",
]
