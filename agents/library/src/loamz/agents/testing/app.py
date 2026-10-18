"""An agent server in process for tests: the real app over a throwaway SQLite file, callers named by headers.

    async with serving(tmp_path) as served:
        alice = served.client("acme", "alice")
        assert (await alice.get("/api/v1/threads")).status_code == 200

Authentication is replaced by three request headers (`x-test-tenant`, `x-test-user`, `x-test-roles`); everything
after it (container, services, stores, error mapping, rate limits) is the real thing.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Request

from loamz.agents.bootstrap import Container, Settings
from loamz.agents.bootstrap.http import caller as caller_dependency
from loamz.agents.bootstrap.http import optional_caller as optional_dependency
from loamz.agents.kernel.caller import Caller
from loamz.agents.server import create_app
from loamz.data.storage.errors import UnauthenticatedError


def header_caller(request: Request) -> Caller:
    tenant = request.headers.get("x-test-tenant")
    user = request.headers.get("x-test-user")
    if not tenant or not user:
        raise UnauthenticatedError("no test caller headers")
    roles = frozenset(filter(None, request.headers.get("x-test-roles", "").split(",")))
    return Caller(tenant_id=tenant, user_id=user, roles=roles)


def optional_header_caller(request: Request) -> Caller | None:
    return header_caller(request) if request.headers.get("x-test-user") else None


@dataclass
class Served:
    app: FastAPI
    container: Container
    base_url: str = "http://t"

    def client(self, tenant: str, user: str, *roles: str, **headers: str) -> httpx.AsyncClient:
        """A client whose requests are made by `user` of `tenant`."""
        head = {"x-test-tenant": tenant, "x-test-user": user, "x-test-roles": ",".join(roles)}
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url=self.base_url,
            headers={**head, **headers},
            timeout=30,
        )

    def anonymous(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url=self.base_url, timeout=30
        )


def settings_for(folder: Path, **sections: Any) -> Settings:
    """Settings for a test: the database, files and sandbox scratch under `folder`; no rate limit unless asked."""
    sections.setdefault("database", {"path": str(folder / "agents.db")})
    files = {"path": str(folder / "files")}
    files.update(sections.pop("files", {}))
    rate = {"enabled": False}
    rate.update(sections.pop("rate_limit", {}))
    return Settings(files=files, rate_limit=rate, **sections)


@asynccontextmanager
async def serving(
    folder: Path,
    *,
    roles: set[str] | None = None,
    container: Container | None = None,
    **sections: Any,
) -> AsyncIterator[Served]:
    """Start the app (its lifespan included) with header-named callers."""
    container = container or Container(settings_for(folder, **sections))
    app = create_app(container, roles=roles or {"all"})
    app.dependency_overrides[caller_dependency] = header_caller
    app.dependency_overrides[optional_dependency] = optional_header_caller
    async with app.router.lifespan_context(app):
        yield Served(app, container)


__all__ = ["Served", "header_caller", "serving", "settings_for"]
