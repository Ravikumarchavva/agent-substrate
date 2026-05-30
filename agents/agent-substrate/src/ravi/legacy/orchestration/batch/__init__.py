"""ravi.fabric.batch — batch config value types + concurrent BatchProcessor.

Generic fan-out infrastructure (concurrency + retries) over a dataset, plus the
config/result dataclasses that describe a batch run.
"""

from ravi.fabric.batch.config import (
    BatchConfig,
    BatchItem,
    BatchResult,
    BatchStatus,
)
from ravi.fabric.batch.processor import BatchProcessor

__all__ = [
    "BatchConfig",
    "BatchItem",
    "BatchResult",
    "BatchStatus",
    "BatchProcessor",
]
