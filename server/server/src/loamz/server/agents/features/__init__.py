"""Features: one folder per bounded context, each with the same shape.

    <context>/<feature>/model.py    value types (pydantic), validation rules
                       store.py    its tables (append-only migrations) and the only SQL that touches them
                       service.py  use cases; takes a verified `Caller`; raises typed errors; no HTTP
                       api.py      thin FastAPI router: parse, call the service, return

A feature uses another only through that feature's `service.py`. `server/` is the only importer of `api.py`.
`FEATURES` lists every router with the roles it belongs to, so one app serves them all (`roles=all`) or a subset
(microservices mode) from the same code.
"""

from __future__ import annotations

from loamz.server.agents.features.automation import FEATURES as AUTOMATION
from loamz.server.agents.features.conversations import FEATURES as CONVERSATIONS
from loamz.server.agents.features.directory import FEATURES as DIRECTORY
from loamz.server.agents.features.feature import Feature
from loamz.server.agents.features.governance import FEATURES as GOVERNANCE
from loamz.server.agents.features.groups import FEATURES as GROUPS
from loamz.server.agents.features.knowledge import FEATURES as KNOWLEDGE
from loamz.server.agents.features.people import FEATURES as PEOPLE
from loamz.server.agents.features.workspace import FEATURES as WORKSPACE

FEATURES: tuple[Feature, ...] = (
    *CONVERSATIONS,
    *WORKSPACE,
    *KNOWLEDGE,
    *DIRECTORY,
    *GROUPS,
    *AUTOMATION,
    *PEOPLE,
    *GOVERNANCE,
)

__all__ = ["FEATURES", "Feature"]
