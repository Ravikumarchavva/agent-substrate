"""Features: one folder per part of the data platform, each with its routers and the engines behind them.

`FEATURES` lists every router with the API role it belongs to, so one process serves them all (`roles=all`) or a
subset (microservices mode) from the same code. A feature uses another only through what that feature exports; only
`app.py` imports the routers. Worker roles (`sql`, `jobs`, `documents`, `inference`) are separate processes.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from importlib import import_module

from fastapi import APIRouter


@dataclass(frozen=True)
class Feature:
    name: str
    role: str
    module: str

    def router(self) -> APIRouter:
        return import_module(f"loamz.server.data.features.{self.module}").router


FEATURES: tuple[Feature, ...] = (
    # Order matters: `catalog.assets` serves `/catalogs/{c}/schemas/{s}/{collection}`, so it comes after every
    # feature with a literal segment in that place (views, materialized views, tables).
    Feature("catalog", "catalog", "catalog.api"),
    Feature("sql", "sql", "sql.api"),
    Feature("catalog.views", "catalog", "catalog.views"),
    Feature("tables.materialized", "tables", "tables.materialized"),
    Feature("tables.maintenance", "tables", "tables.maintenance"),
    Feature("catalog.policies", "catalog", "catalog.policies"),
    Feature("tables.imports", "tables", "tables.imports"),
    Feature("catalog.governance", "catalog", "catalog.governance"),
    Feature("tables.pipelines", "tables", "tables.pipelines"),
    Feature("compute", "compute", "compute.api"),
    Feature("catalog.assets", "catalog", "catalog.assets"),
    Feature("files", "files", "files.api"),
    Feature("documents", "documents", "documents.api"),
    Feature("memory", "memory", "memory.api"),
    Feature("privacy", "privacy", "privacy.api"),
    Feature("tenants", "tenants", "tenants.api"),
)

ROLES: frozenset[str] = frozenset(f.role for f in FEATURES)


def selected(roles: Callable[[str], bool]) -> list[Feature]:
    return [f for f in FEATURES if roles(f.role)]
