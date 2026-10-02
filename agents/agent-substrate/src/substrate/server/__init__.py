"""substrate.server — an agent over HTTP: ``create_app(agent)``.

SSE in the engine's own wire protocol (``/chat``), AG-UI for front ends that speak it (``/agui``), resumable run
streams, cancellation, and durable human approvals. Needs the ``serve`` extra (FastAPI); see ``app.py``.
"""

from __future__ import annotations

from substrate.server.app import ChatRequest, create_app
from substrate.server.loader import load

__all__ = ["ChatRequest", "create_app", "load"]
