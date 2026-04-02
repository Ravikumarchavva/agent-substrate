"""ASGI app serving Restate workflow handlers.

Run via ``uvicorn raavan.distributed.restate_app:app`` or from the
:mod:`raavan.distributed.worker` entry-point.
"""

from __future__ import annotations

import restate

from raavan.distributed.workflow import agent_workflow

app = restate.app(services=[agent_workflow])
