"""Input-safety contracts: what a text/image classifier returns, and the
Protocol shape agents-layer guardrails depend on.

Lives in kernel (not agents/integrations) because both a TURN-stage
middleware (agents, L1) and its concrete model-backed implementation
(integrations, L2) need the exact same shape — the classic reason kernel
holds a Protocol: multiple layers need it, and it has zero I/O/deps of its
own. The middleware never imports a concrete classifier; it only ever sees
these two Protocols, injected from ``substrate_cloud/factory.py``.

Deliberately NOT here: any actual model, tokenizer, or inference code — all
of that lives in ``integrations/safety/``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Mapping, Protocol, runtime_checkable


class Severity(StrEnum):
    """How serious a safety verdict is, ordered ``NONE < LOW < MEDIUM < HIGH < CRITICAL``."""

    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return _ORDER.index(self)

    def __lt__(self, other: object) -> bool:
        return self.rank < other.rank if isinstance(other, Severity) else NotImplemented

    def __le__(self, other: object) -> bool:
        return (
            self.rank <= other.rank if isinstance(other, Severity) else NotImplemented
        )

    def __gt__(self, other: object) -> bool:
        return self.rank > other.rank if isinstance(other, Severity) else NotImplemented

    def __ge__(self, other: object) -> bool:
        return (
            self.rank >= other.rank if isinstance(other, Severity) else NotImplemented
        )


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
    return a if a >= b else b


@dataclass(frozen=True)
class SafetyVerdict:
    """One classifier's opinion on one piece of input.

    ``scores`` is multi-label (``{"malicious": 0.97}`` or ``{"sexual": 0.8,
    "violence": 0.1}``), never a single forced category — a detector that
    only supports one axis just returns one key; nothing here should coax a
    classifier into claiming coverage it doesn't have.
    """

    severity: Severity
    scores: Mapping[str, float] = field(default_factory=dict)
    detector: str = ""
    modality: str = "text"  # "text" | "image" | "document"
    detail: str = ""

    @property
    def flagged(self) -> bool:
        return self.severity != Severity.NONE


@runtime_checkable
class TextSafetyClassifier(Protocol):
    """Implemented by integrations/safety/text_classifier.py concrete
    classes. Sync, not async — CPU-bound ONNX inference, not I/O; callers
    run it via ``asyncio.to_thread``."""

    def classify(self, text: str) -> SafetyVerdict: ...


@runtime_checkable
class ImageSafetyClassifier(Protocol):
    """Implemented by integrations/safety/image_classifier.py."""

    def classify(self, image_bytes: bytes) -> SafetyVerdict: ...


__all__ = [
    "Severity",
    "max_severity",
    "SafetyVerdict",
    "TextSafetyClassifier",
    "ImageSafetyClassifier",
]
