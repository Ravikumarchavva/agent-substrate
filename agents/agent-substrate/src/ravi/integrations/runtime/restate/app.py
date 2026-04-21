"""Restate ASGI application — serves all workflow handlers.

Run via uvicorn::

    uvicorn ravi.integrations.runtime.restate.app:app --port 9080
"""

from __future__ import annotations

import restate

from ravi.integrations.runtime.restate.workflows import (
    agent_workflow,
    chain_workflow,
    pipeline_workflow,
)

app = restate.app(services=[pipeline_workflow, chain_workflow, agent_workflow])
