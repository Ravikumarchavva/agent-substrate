"""ravi.fabric.batch — Concurrent BatchProcessor implementation.

Generic fan-out infrastructure (concurrency + retries) over a dataset. The
batch config value types live in :mod:`ravi.kernel.batch`.
"""

from ravi.fabric.batch.processor import BatchProcessor

__all__ = ["BatchProcessor"]
