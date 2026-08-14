"""Retrieval-quality eval harness for the RAG pipeline.

See ``metrics.py`` for the per-stage metric functions, ``dataset.py`` for the
starter labeled query set, and ``runner.py`` for wiring them against a real
``PgVectorStore``. ``test_metrics.py`` unit-tests the metric math with no
infra; ``test_retrieval_eval.py`` runs the full stage-by-stage eval against a
real Postgres (skips if unreachable) — see that file's module docstring for
scope and how to grow this past the starter dataset.
"""

from __future__ import annotations
