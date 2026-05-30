from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Generator

logger = logging.getLogger(__name__)


@contextmanager
def agent_span(agent_id: str, operation_name: str) -> Generator[None, None, None]:
    """Lightweight tracing span for an agent operation.

    In production this wraps an OpenTelemetry span tied to the causal
    execution tree.  Locally it logs start/end at DEBUG level.
    """
    logger.debug("[TRACE START] %s: %s", agent_id, operation_name)
    try:
        yield
    except Exception:
        logger.exception("[TRACE ERROR] %s: %s", agent_id, operation_name)
        raise
    finally:
        logger.debug("[TRACE END] %s: %s", agent_id, operation_name)
