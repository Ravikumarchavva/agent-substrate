from __future__ import annotations

import json
import re

from loamz.agents.bootstrap.container import Container
from loamz.agents.features.workspace.artifacts.model import (
    ArtifactCreate,
    ArtifactList,
    ArtifactOut,
    ArtifactPatch,
)
from loamz.agents.kernel.caller import Caller
from loamz.agents.kernel.ports.files import FileStore
from loamz.data.storage.errors import NotFoundError


def _slugify(text: str) -> str:
    cleaned = re.sub(r"[^\w\s-]", "", text.lower())
    return re.sub(r"[-\s]+", "-", cleaned).strip("-") or "artifact"


def _artifact_key(
    tenant_id: str, user_id: str, scope: str, slug: str, thread_id: str | None = None
) -> str:
    if scope == "session" and thread_id:
        return (
            f"tenants/{tenant_id}/users/{user_id}/conversations/{thread_id}/artifacts/{slug}.json"
        )
    return f"tenants/{tenant_id}/users/{user_id}/artifacts/{slug}.json"


def _artifact_prefix(tenant_id: str, user_id: str, scope: str, thread_id: str | None = None) -> str:
    if scope == "session" and thread_id:
        return f"tenants/{tenant_id}/users/{user_id}/conversations/{thread_id}/artifacts/"
    return f"tenants/{tenant_id}/users/{user_id}/artifacts/"


class ArtifactsService:
    def __init__(self, container: Container) -> None:
        self._c = container

    @property
    def _files(self) -> FileStore:
        return self._c.file_store

    async def list(
        self,
        caller: Caller,
        *,
        scope: str = "global",
        thread_id: str | None = None,
        type: str | None = None,
        tag: str | None = None,
    ) -> ArtifactList:
        prefix = _artifact_prefix(caller.tenant_id, caller.user_id, scope, thread_id)
        files = await self._files.list(prefix)
        items: list[ArtifactOut] = []
        for f in files:
            if not f.key.endswith(".json"):
                continue
            try:
                data = await self._files.download(f.key)
                art = ArtifactOut(**json.loads(data))
                if type and art.type != type:
                    continue
                if tag and tag not in art.tags:
                    continue
                items.append(art)
            except Exception:
                continue
        return ArtifactList(items=items)

    async def get(
        self,
        caller: Caller,
        slug: str,
        *,
        scope: str = "global",
        thread_id: str | None = None,
    ) -> ArtifactOut:
        key = _artifact_key(caller.tenant_id, caller.user_id, scope, slug, thread_id)
        try:
            raw = await self._files.download(key)
            return ArtifactOut(**json.loads(raw))
        except NotFoundError as err:
            raise NotFoundError(f"Artifact '{slug}' not found") from err

    async def create(
        self,
        caller: Caller,
        body: ArtifactCreate,
        *,
        scope: str = "global",
        thread_id: str | None = None,
    ) -> ArtifactOut:
        slug = _slugify(body.title or body.type)
        key = _artifact_key(caller.tenant_id, caller.user_id, scope, slug, thread_id)
        art = ArtifactOut(
            slug=slug,
            scope=scope,
            type=body.type,
            title=body.title,
            description=body.description,
            body=body.body,
            tags=body.tags,
            resource=body.resource,
        )
        await self._files.upload(
            key, art.model_dump_json().encode(), content_type="application/json"
        )
        return art

    async def patch(
        self,
        caller: Caller,
        slug: str,
        body: ArtifactPatch,
        *,
        scope: str = "global",
        thread_id: str | None = None,
    ) -> ArtifactOut:
        art = await self.get(caller, slug, scope=scope, thread_id=thread_id)
        updated_dict = art.model_dump()
        if body.title is not None:
            updated_dict["title"] = body.title
        if body.description is not None:
            updated_dict["description"] = body.description
        if body.body is not None:
            updated_dict["body"] = body.body
        if body.tags is not None:
            updated_dict["tags"] = body.tags
        if body.status is not None:
            updated_dict["status"] = body.status
        updated = ArtifactOut(**updated_dict)
        key = _artifact_key(caller.tenant_id, caller.user_id, scope, slug, thread_id)
        await self._files.upload(
            key, updated.model_dump_json().encode(), content_type="application/json"
        )
        return updated

    async def promote(self, caller: Caller, slug: str, thread_id: str) -> ArtifactOut:
        art = await self.get(caller, slug, scope="session", thread_id=thread_id)
        promoted = ArtifactOut(
            slug=art.slug,
            scope="global",
            type=art.type,
            title=art.title,
            description=art.description,
            body=art.body,
            tags=art.tags,
            resource=art.resource,
            status=art.status,
            trust=art.trust,
        )
        global_key = _artifact_key(caller.tenant_id, caller.user_id, "global", slug)
        await self._files.upload(
            global_key, promoted.model_dump_json().encode(), content_type="application/json"
        )
        return promoted

    async def delete(
        self,
        caller: Caller,
        slug: str,
        *,
        scope: str = "global",
        thread_id: str | None = None,
    ) -> None:
        key = _artifact_key(caller.tenant_id, caller.user_id, scope, slug, thread_id)
        await self._files.delete(key)

    async def erase(self, tenant_id: str, user_id: str | None = None) -> int:
        return 0


def artifacts(container: Container) -> ArtifactsService:
    return ArtifactsService(container)
