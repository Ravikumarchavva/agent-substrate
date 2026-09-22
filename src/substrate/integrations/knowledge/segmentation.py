"""Sentence segmentation strategies for chunking.

Chunkers pack *sentences* into chunks, so the quality of a chunk boundary is
bounded by the quality of the sentence boundaries it is packing. This module
is the seam that decides how those boundaries are found.

The default :class:`RegexSegmenter` reproduces the behaviour this package had
before the seam existed. :class:`SaTSegmenter` swaps in a purpose-built
segmentation model for text where punctuation is not a reliable signal.

Usage::

    from substrate.capabilities.knowledge.segmentation import SaTSegmenter
    from substrate.capabilities.knowledge.chunking import StructureAwareChunker

    chunker = StructureAwareChunker(segmenter=SaTSegmenter())
"""

from __future__ import annotations

import re
import threading
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SentenceSegmenter(Protocol):
    """Splits text into sentences.

    Implementations must return already-stripped, non-empty strings —
    callers pack the result directly without re-normalising it.
    """

    def segment(self, text: str) -> list[str]: ...


class RegexSegmenter:
    """Splits on whitespace following ``.``, ``!`` or ``?``.

    Cheap, dependency-free, and correct for well-punctuated prose. It is the
    default for exactly that reason — but note what it cannot see: a markdown
    heading, a bullet list item, a table cell, and a line broken mid-sentence
    by a PDF's column layout all lack terminal punctuation, so this returns
    them fused into one "sentence" no matter how long it grows. For OCR'd
    documents that is the common case rather than the exception, which is
    what :class:`SaTSegmenter` exists to address.
    """

    _SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")

    def segment(self, text: str) -> list[str]:
        return [s.strip() for s in self._SENTENCE_RE.split(text) if s.strip()]


class SaTSegmenter:
    """Segment-any-Text (SaT) model-based segmentation, via ``wtpsplit``.

    SaT is punctuation-agnostic and multilingual, so it finds boundaries in
    exactly the text :class:`RegexSegmenter` fuses: headings, list items, and
    layout-broken lines. Requires the ``chunking`` extra
    (``uv sync --extra chunking``); the import is deferred to construction so
    that merely importing this module never requires the dependency.

    ``sat-3l-sm`` is upstream's recommended speed/accuracy balance.

    The errors it does make are the harmless kind. Measured on the OCR-shaped
    fixture in ``tests/capabilities/test_chunking.py``, SaT splits a heading
    one phrase early ("Scope 1 and Scope 2" / "Emissions Summary") while the
    regex returns the heading and all four following lines fused into one
    unit. Over-segmentation costs nothing here because the chunker packs
    units back up to ``chunk_size`` anyway; under-segmentation is
    unrecoverable, because a unit larger than ``chunk_size`` has no boundary
    left to pack on. That asymmetry is the whole argument for this class.

    Runs via ONNX on CPU by default. ``wtpsplit`` itself declares no deep
    learning backend — it needs either ``torch`` or ``onnxruntime`` present,
    and raises "Please install `torch` to use WtP with a PyTorch model" if
    neither is. ONNX is the default here because it is by far the lighter of
    the two, and because segmentation is not the bottleneck in an ingestion
    pipeline whose extraction stage is GPU-bound and takes tens of seconds
    per document — keeping this off the GPU leaves that memory to extraction
    and embedding. Pass ``ort_providers=None`` to take the torch path
    instead, and any other keyword argument goes straight to ``SaT``.
    """

    def __init__(
        self,
        model: str = "sat-3l-sm",
        *,
        ort_providers: list[str] | None = ("CPUExecutionProvider",),  # type: ignore[assignment]
        **kwargs: Any,
    ) -> None:
        try:
            from wtpsplit import SaT
        except ImportError as exc:  # pragma: no cover - depends on install
            raise ImportError(
                "SaTSegmenter requires the 'wtpsplit' package. Install it with "
                "`uv sync --extra chunking`, or use RegexSegmenter instead."
            ) from exc

        self.model_name = model
        if ort_providers is not None:
            kwargs["ort_providers"] = list(ort_providers)
        self._sat = SaT(model, **kwargs)
        # SaT wraps a transformer whose tokenizer and inference buffers are
        # not documented as thread-safe, and this runs under asyncio.to_thread
        # with several concurrent ingestion workers. The call is fast enough
        # (upstream benchmarks ~95ms per 1000 texts) that serialising it is
        # immaterial next to per-document extraction time, so take the safe
        # option rather than debugging a rare corruption later.
        self._lock = threading.Lock()

    def segment(self, text: str) -> list[str]:
        if not text.strip():
            return []
        with self._lock:
            pieces = self._sat.split(text)
        return [s.strip() for s in pieces if s and s.strip()]
