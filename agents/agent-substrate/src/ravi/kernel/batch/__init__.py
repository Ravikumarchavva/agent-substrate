"""ravi.kernel.batch — Batch processing config dataclasses only.

The ``BatchProcessor`` lives in :mod:`ravi.fabric.batch`.
"""

from __future__ import annotations

from ravi.kernel.batch.config import BatchConfig, BatchItem, BatchResult

__all__ = ["BatchConfig", "BatchItem", "BatchResult"]
