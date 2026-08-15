"""Input-safety contracts: what a text/image classifier returns, and the
Protocol shape agents-layer guardrails depend on.

Lives in kernel (not agents/capabilities) because both a TURN-stage
middleware (agents, L1) and its concrete model-backed implementation
(capabilities, L2) need the exact same shape — the classic reason kernel
holds a Protocol: multiple layers need it, and it has zero I/O/deps of its
own. The middleware never imports a concrete classifier; it only ever sees
these two Protocols, injected from ``infrastructure/serving_factory.py``.

Deliberately NOT here: any actual model, tokenizer, or inference code — all
of that lives in ``capabilities/safety/``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol


class Severity(str, Enum):
    """How serious a safety verdict is. Ordered low → high — comparisons use
    ``_ORDER.index(...)``, not enum identity, so callers can rank verdicts."""

    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


_ORDER = [
    Severity.NONE,
    Severity.LOW,
    Severity.MEDIUM,
    Severity.HIGH,
    Severity.CRITICAL,
]


def max_severity(a: Severity, b: Severity) -> Severity:
    """The more severe of two verdicts — used to aggregate across modalities
    (text + image in one turn) and across document chunks."""
    return a if _ORDER.index(a) >= _ORDER.index(b) else b


@dataclass(frozen=True)
class SafetyVerdict:
    """One classifier's opinion on one piece of input.

    ``scores`` is multi-label (``{"malicious": 0.97}`` or ``{"sexual": 0.8,
    "violence": 0.1}``), never a single forced category — a detector that
    only supports one axis just returns one key; nothing here should coax a
    classifier into claiming coverage it doesn't have.
    """

    severity: Severity
    scores: dict[str, float] = field(default_factory=dict)
    detector: str = ""
    modality: str = "text"  # "text" | "image" | "document"
    detail: str = ""

    @property
    def flagged(self) -> bool:
        return self.severity != Severity.NONE


class TextSafetyClassifier(Protocol):
    """Implemented by capabilities/safety/text_classifier.py concrete
    classes. Sync, not async — CPU-bound ONNX inference, not I/O; callers
    run it via ``asyncio.to_thread``."""

    def classify(self, text: str) -> SafetyVerdict: ...


class ImageSafetyClassifier(Protocol):
    """Implemented by capabilities/safety/image_classifier.py."""

    def classify(self, image_bytes: bytes) -> SafetyVerdict: ...


__all__ = [
    "Severity",
    "max_severity",
    "SafetyVerdict",
    "TextSafetyClassifier",
    "ImageSafetyClassifier",
]
