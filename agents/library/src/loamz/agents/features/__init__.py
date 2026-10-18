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

from collections.abc import Callable
from dataclasses import dataclass

from fastapi import APIRouter


@dataclass(frozen=True)
class Feature:
    name: str
    roles: frozenset[str]
    router: Callable[[], APIRouter]


def _people_preferences() -> APIRouter:
    from loamz.agents.features.people.preferences.api import router

    return router


FEATURES: tuple[Feature, ...] = (
    Feature("people.preferences", frozenset({"gateway"}), _people_preferences),
)
