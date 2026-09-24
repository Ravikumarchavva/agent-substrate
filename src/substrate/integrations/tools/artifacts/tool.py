"""ArtifactsTool — lets the agent read, save and promote curated artifacts.

Scoping is taken from the run's ``ctx.scope`` (tenant / user / thread), never
from tool arguments:
a model-supplied user or tenant id would be an authorization hole, since
the model can be steered by the documents it reads.

Default scope is ``session``. Writing straight to ``global`` is allowed but
is a deliberate, separate choice the model has to make, and promotion is
recorded in the bundle's ``log.md`` — so nothing reaches durable,
cross-conversation memory without an auditable step.
"""

from __future__ import annotations

from typing import Any

from substrate.integrations.artifacts.store import ArtifactStore
from substrate.kernel import TextBlock
from substrate.kernel.agent.runtime_context import RunScope, scope_of
from substrate.kernel.tools import ToolExecutionResult, ToolType
from substrate.logger import setup_logging

logger = setup_logging("substrate.integrations.tools.artifacts")

_DEFAULT_SESSION = "default"


def _error(message: str) -> ToolExecutionResult:
    return ToolExecutionResult(content=[TextBlock(text=message)], is_error=True)


class ArtifactsTool:
    """Save, list, read and promote artifacts (including memory)."""

    tool_type = ToolType.KNOWLEDGE
    name: str = "artifacts"
    description: str = (
        "Store and retrieve durable knowledge as artifacts. "
        "action=save: record something worth keeping (a user preference, a "
        "decision, a conclusion) — use type='Memory' for facts about the "
        "user. action=list: see existing artifacts. action=get: read one in "
        "full. action=promote: move a session artifact into the user's "
        "global store so it survives this conversation. action=forget: "
        "mark one deprecated. "
        "scope='session' (default) is this conversation only; "
        "scope='global' persists across all conversations. "
        "Save a global artifact only for things that stay true beyond this "
        "conversation; keep working notes in session scope."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["save", "list", "get", "promote", "forget"],
                "description": "What to do.",
            },
            "scope": {
                "type": "string",
                "enum": ["session", "global"],
                "description": "session = this conversation; global = all conversations.",
            },
            "content": {
                "type": "string",
                "description": "The artifact body (required for save).",
            },
            "title": {
                "type": "string",
                "description": "Short human-readable name (recommended for save).",
            },
            "type": {
                "type": "string",
                "description": "Concept type, e.g. Memory, Decision, Note. Default Memory.",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Tags for later filtering.",
            },
            "slug": {
                "type": "string",
                "description": "Identifier of an existing artifact (get/promote/forget).",
            },
        },
        "required": ["action"],
    }

    def __init__(self, store: ArtifactStore, *, model_name: str = "unknown") -> None:
        self._store = store
        # OKF actor string for provenance — an agent-written concept is
        # Machine-confirmed until a human verifies it.
        self._actor = f"substrate/{model_name}"

    def _prefix(self, scope: str, run: RunScope) -> tuple[str | None, str | None]:
        """Resolve (prefix, error). Identity comes from the run context only."""
        tenant_id = run.tenant_id
        if not tenant_id:
            return None, "Artifacts need a tenant-scoped conversation."
        user_id = run.user_id
        if not user_id:
            return None, "Artifacts need a signed-in user."
        if scope == "global":
            return self._store.scope_prefix(tenant_id, user_id), None
        thread_id = run.thread_id
        if not thread_id or thread_id == _DEFAULT_SESSION:
            return None, "Session artifacts need an active conversation."
        return self._store.scope_prefix(tenant_id, user_id, conversation_id=thread_id), None

    async def execute(self, *, ctx: Any = None, **kwargs: Any) -> ToolExecutionResult:
        action = str(kwargs.get("action") or "").strip()
        scope = str(kwargs.get("scope") or "session").strip()
        if scope not in {"session", "global"}:
            return _error("scope must be 'session' or 'global'.")

        prefix, err = self._prefix(scope, scope_of(ctx))
        if err or prefix is None:
            return _error(err or "Artifact scope unavailable.")

        try:
            if action == "save":
                return await self._save(prefix, scope, kwargs)
            if action == "list":
                return await self._list(prefix, scope, kwargs)
            if action == "get":
                return await self._get(prefix, kwargs)
            if action == "promote":
                return await self._promote(kwargs)
            if action == "forget":
                return await self._forget(prefix, kwargs)
        except Exception as exc:  # noqa: BLE001 - surface as a tool error, never crash the run
            logger.error("artifacts tool %s failed: %s", action, exc)
            return _error(f"Artifact operation failed: {exc}")

        return _error(f"Unknown action {action!r}.")

    async def _save(self, prefix: str, scope: str, kw: dict[str, Any]) -> ToolExecutionResult:
        content = str(kw.get("content") or "").strip()
        if not content:
            return _error("save requires 'content'.")
        title = kw.get("title") or content[:60]
        slug, _ = await self._store.create(
            prefix,
            concept_type=str(kw.get("type") or "Memory"),
            title=str(title),
            body=content,
            tags=[str(t) for t in (kw.get("tags") or [])],
            author=self._actor,
        )
        return ToolExecutionResult(
            content=[TextBlock(text=f"Saved {scope} artifact '{slug}'.")]
        )

    async def _list(self, prefix: str, scope: str, kw: dict[str, Any]) -> ToolExecutionResult:
        refs = await self._store.list(
            prefix,
            scope=scope,
            concept_type=kw.get("type"),
            tags=[str(t) for t in (kw.get("tags") or [])] or None,
        )
        if not refs:
            return ToolExecutionResult(
                content=[TextBlock(text=f"No {scope} artifacts yet.")]
            )
        lines = [
            f"- {r.slug} [{r.concept.type}] {r.concept.title or ''}"
            + (f" tags={','.join(r.concept.tags)}" if r.concept.tags else "")
            for r in refs
        ]
        return ToolExecutionResult(
            content=[TextBlock(text=f"{scope} artifacts:\n" + "\n".join(lines))]
        )

    async def _get(self, prefix: str, kw: dict[str, Any]) -> ToolExecutionResult:
        slug = str(kw.get("slug") or "").strip()
        if not slug:
            return _error("get requires 'slug'.")
        concept = await self._store.get(prefix, slug)
        if concept is None:
            return _error(f"No artifact '{slug}'.")
        header = f"{concept.title or slug} [{concept.type}]"
        return ToolExecutionResult(
            content=[TextBlock(text=f"{header}\n\n{concept.body}")]
        )

    async def _promote(self, kw: dict[str, Any]) -> ToolExecutionResult:
        slug = str(kw.get("slug") or "").strip()
        if not slug:
            return _error("promote requires 'slug'.")
        session_prefix, err = self._prefix("session")
        if err or session_prefix is None:
            return _error(err or "No active conversation to promote from.")
        global_prefix, err = self._prefix("global")
        if err or global_prefix is None:
            return _error(err or "No user to promote into.")

        new_slug = await self._store.promote(session_prefix, global_prefix, slug)
        if new_slug is None:
            return _error(f"No session artifact '{slug}'.")
        return ToolExecutionResult(
            content=[TextBlock(text=f"Promoted '{slug}' to global as '{new_slug}'.")]
        )

    async def _forget(self, prefix: str, kw: dict[str, Any]) -> ToolExecutionResult:
        slug = str(kw.get("slug") or "").strip()
        if not slug:
            return _error("forget requires 'slug'.")
        # Deprecate, not delete: a superseded fact keeps its history.
        if not await self._store.deprecate(prefix, slug):
            return _error(f"No artifact '{slug}'.")
        return ToolExecutionResult(content=[TextBlock(text=f"Marked '{slug}' deprecated.")])


__all__ = ["ArtifactsTool"]
