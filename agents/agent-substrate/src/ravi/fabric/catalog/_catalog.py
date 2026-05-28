"""AgentCatalog — unified resource governance for the agent runtime.

Single source of truth for all registered resources: tools, skills, memories,
contexts, checkpoints, MCP tools, and models.

FQN format: ``{catalog}.{schema}.{name}``
"""

from __future__ import annotations

import fnmatch
import logging
from typing import Any, Callable, Dict, Iterable, List, Optional, Set

from ravi.fabric.catalog._spec import (
    ResourceSpec,
    ResourceType,
    SkillManagerProtocol,
)
from ravi.fabric.catalog.lazy_tool import LazyTool
from ravi.kernel.tools.base_tool import BaseTool, ToolRisk

logger = logging.getLogger(__name__)


class AgentCatalog:
    """Unified three-level namespace catalog for agent runtime resources.

    Namespace format: ``{catalog}.{schema}.{name}``

    Usage::

        catalog = AgentCatalog()
        spec = ResourceSpec.for_tool("tax_calc", schema="finance", description="...")
        catalog.register(spec, my_tax_tool)

        tool = catalog.get_tool("tax_calc")
    """

    def __init__(self, default_catalog: str = "main") -> None:
        self.default_catalog = default_catalog
        # FQN → (spec, instance)
        self._resources: Dict[str, tuple[ResourceSpec, Any]] = {}
        # FQN insertion order for deterministic listing
        self._insertion_order: List[str] = []
        # alias → FQN
        self._aliases: Dict[str, str] = {}
        # principal → {pattern → set[privilege]}
        self._grants: Dict[str, Dict[str, Set[str]]] = {}
        # SkillManager compat (populated by init_skills)
        self.skill_manager: Optional[Any] = None

    # ── Core registration ────────────────────────────────────────────────────

    def register(self, spec: ResourceSpec, instance: Any) -> "AgentCatalog":
        """Register a resource.

        Raises ``ValueError`` on FQN collision or alias collision. The
        alias check prevents one resource from silently shadowing another
        when both declare the same alias.
        """
        fqn = spec.fqn
        if fqn in self._resources:
            raise ValueError(
                f"Catalog collision: '{fqn}' is already registered. "
                "Use unregister() first if replacement is intended."
            )
        # Detect alias collision before any mutation
        for alias in spec.aliases:
            existing = self._aliases.get(alias.lower())
            if existing is not None and existing != fqn:
                raise ValueError(
                    f"Catalog collision: alias '{alias}' already points to "
                    f"'{existing}'. Pick a unique alias for '{fqn}'."
                )
        self._resources[fqn] = (spec, instance)
        self._insertion_order.append(fqn)
        for alias in spec.aliases:
            self._aliases[alias.lower()] = fqn
        logger.debug("AgentCatalog: registered %s '%s'", spec.resource_type.value, fqn)
        return self

    def unregister(self, name: str) -> None:
        """Remove a resource by FQN or short-name."""
        fqn = self._find_fqn(name)
        if fqn and fqn in self._resources:
            spec, _ = self._resources.pop(fqn)
            self._insertion_order.remove(fqn)
            # Clean up aliases
            for alias in spec.aliases:
                self._aliases.pop(alias.lower(), None)

    # ── Resolution ───────────────────────────────────────────────────────────

    def resolve(
        self,
        name: str,
        *,
        principal: Optional[str] = None,
        privilege: str = "execute",
        search_path: Optional[List[str]] = None,
    ) -> Optional[Any]:
        """Resolve a name to its registered instance.

        Enforces permissions when ``principal`` is supplied.
        Returns ``None`` if the resource does not exist.
        Raises ``PermissionError`` if ``principal`` is set but lacks the privilege.
        """
        fqn = self._find_fqn(name, search_path)
        if fqn is None:
            return None
        if principal is not None and not self.check_permission(
            principal, fqn, privilege
        ):
            raise PermissionError(
                f"Principal '{principal}' lacks '{privilege}' on '{fqn}'"
            )
        _, instance = self._resources[fqn]
        return instance

    def get_spec(
        self, name: str, search_path: Optional[List[str]] = None
    ) -> Optional[ResourceSpec]:
        """Return the ResourceSpec for a resource (without the instance)."""
        fqn = self._find_fqn(name, search_path)
        if fqn is None:
            return None
        spec, _ = self._resources[fqn]
        return spec

    # ── Backward-compatible typed accessors ──────────────────────────────────

    def get(self, name: str, search_path: Optional[List[str]] = None) -> Optional[Any]:
        """Return the raw (spec, instance) tuple or None if not found.

        Kept for backward compatibility with code that unpacks CatalogAsset.
        Prefer ``resolve()`` for new code.
        """
        fqn = self._find_fqn(name, search_path)
        if fqn is None:
            return None
        return _LegacyAssetView(*self._resources[fqn])

    def get_tool(
        self, name: str, search_path: Optional[List[str]] = None
    ) -> Optional[BaseTool]:
        """Return a tool instance by FQN or short-name."""
        fqn = self._find_fqn(name, search_path)
        if fqn is None:
            return None
        spec, inst = self._resources[fqn]
        return (
            inst
            if spec.resource_type in (ResourceType.TOOL, ResourceType.MCP_TOOL)
            else None
        )

    def get_memory(
        self, name: str, search_path: Optional[List[str]] = None
    ) -> Optional[Any]:
        """Return a memory instance by FQN or short-name."""
        fqn = self._find_fqn(name, search_path)
        if fqn is None:
            return None
        spec, inst = self._resources[fqn]
        return inst if spec.resource_type == ResourceType.MEMORY else None

    def get_context(
        self, name: str, search_path: Optional[List[str]] = None
    ) -> Optional[Any]:
        """Return a model context instance by FQN or short-name."""
        fqn = self._find_fqn(name, search_path)
        if fqn is None:
            return None
        spec, inst = self._resources[fqn]
        return inst if spec.resource_type == ResourceType.CONTEXT else None

    def get_checkpoint_store(
        self, name: str, search_path: Optional[List[str]] = None
    ) -> Optional[Any]:
        """Return a checkpoint store by FQN or short-name."""
        fqn = self._find_fqn(name, search_path)
        if fqn is None:
            return None
        spec, inst = self._resources[fqn]
        return inst if spec.resource_type == ResourceType.CHECKPOINT else None

    def get_model(
        self, name: str, search_path: Optional[List[str]] = None
    ) -> Optional[Any]:
        """Return a registered model client by FQN or short-name."""
        fqn = self._find_fqn(name, search_path)
        if fqn is None:
            return None
        spec, inst = self._resources[fqn]
        return inst if spec.resource_type == ResourceType.MODEL else None

    # ── Primary resource accessors ───────────────────────────────────────────
    # Return the first registered instance of each resource type. These are
    # the "default" resources the agent uses when no explicit name is given.

    def primary_model(self) -> Optional[Any]:
        """Return the first registered model client, or None."""
        for _, inst in self._by_type(ResourceType.MODEL):
            return inst
        return None

    def primary_memory(self) -> Optional[Any]:
        """Return the first registered memory backend, or None."""
        for _, inst in self._by_type(ResourceType.MEMORY):
            return inst
        return None

    def primary_context(self) -> Optional[Any]:
        """Return the first registered model context, or None."""
        for _, inst in self._by_type(ResourceType.CONTEXT):
            return inst
        return None

    def primary_checkpoint_store(self) -> Optional[Any]:
        """Return the first registered checkpoint store, or None."""
        for _, inst in self._by_type(ResourceType.CHECKPOINT):
            return inst
        return None

    def get_asset(self, fqn: str) -> Optional[Any]:
        """Return a legacy asset view by exact FQN (for backward compat)."""
        if fqn not in self._resources:
            return None
        return _LegacyAssetView(*self._resources[fqn])

    def __contains__(self, name: str) -> bool:
        return self._find_fqn(name) is not None

    # ── Listing queries ──────────────────────────────────────────────────────

    def all_tools(self) -> List[BaseTool]:
        """All registered tool instances in insertion order."""
        return [
            inst for _, inst in self._by_type(ResourceType.TOOL, ResourceType.MCP_TOOL)
        ]

    def all_skills(self) -> List[Any]:
        """All registered skill metadata objects in insertion order."""
        return [inst for _, inst in self._by_type(ResourceType.SKILL)]

    def by_risk(self, risk: ToolRisk) -> List[Any]:
        """Return legacy asset views for all tools matching ``risk``."""
        result = []
        for fqn in self._insertion_order:
            spec, inst = self._resources[fqn]
            if spec.resource_type in (ResourceType.TOOL, ResourceType.MCP_TOOL):
                tool_risk = getattr(inst, "risk", None)
                if tool_risk == risk:
                    result.append(_LegacyAssetView(spec, inst))
        return result

    # ── Search ───────────────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        *,
        limit: int = 8,
        kind_filter: Optional[str] = None,
        exclude_names: Optional[Set[str]] = None,
    ) -> List[Any]:
        """Word-level search across name, description, tags, category, aliases."""
        tokens = query.lower().split()
        if not tokens:
            return []
        results = []
        for fqn in self._insertion_order:
            spec, inst = self._resources[fqn]
            if exclude_names and (spec.name in exclude_names or fqn in exclude_names):
                continue
            if kind_filter and spec.resource_type.value != kind_filter:
                continue
            corpus = " ".join(
                [
                    spec.name,
                    fqn,
                    spec.description,
                    spec.category,
                    *spec.tags,
                    *spec.aliases,
                ]
            ).lower()
            if any(token in corpus for token in tokens):
                results.append(_LegacyAssetView(spec, inst))
            if len(results) >= limit:
                break
        return results

    # ── FQN resolution ───────────────────────────────────────────────────────

    def _find_fqn(
        self, name: str, search_path: Optional[List[str]] = None
    ) -> Optional[str]:
        """Resolve a name to a canonical FQN, or None."""
        lower = name.lower()

        # Already an FQN (3 parts)
        if lower.count(".") == 2 and lower in self._resources:
            return lower

        # Alias lookup
        if lower in self._aliases:
            return self._aliases[lower]

        # Search path scan (short-name → schemas in default catalog)
        path = search_path or ["default", "system"]
        for schema in path:
            candidate = f"{self.default_catalog}.{schema}.{lower}"
            if candidate in self._resources:
                return candidate

        # Last resort: any schema in the default catalog — only when caller
        # did not specify an explicit search_path (i.e. open-ended lookup).
        if search_path is None:
            prefix = f"{self.default_catalog}."
            for fqn in self._insertion_order:
                if fqn.endswith(f".{lower}") and fqn.startswith(prefix):
                    return fqn

        return None

    def resolve_fqn(self, name: str, search_path: List[str]) -> Optional[str]:
        """Backward-compat wrapper for ``_find_fqn``."""
        return self._find_fqn(name, search_path)

    # ── Access control ───────────────────────────────────────────────────────

    def grant_privilege(self, privilege: str, target: str, principal: str) -> None:
        """Grant ``privilege`` on ``target`` pattern to ``principal``."""
        rules = self._grants.setdefault(principal.lower(), {})
        rules.setdefault(target.lower(), set()).add(privilege.lower())
        logger.info(
            "AgentCatalog: GRANTED '%s' on '%s' to '%s'", privilege, target, principal
        )

    def check_permission(
        self, principal: str, target_fqn: str, privilege: str = "execute"
    ) -> bool:
        """Return True when ``principal`` has ``privilege`` on ``target_fqn``.

        Open access when no grants are configured.
        Supports wildcard patterns (e.g. ``main.finance.*``).
        """
        if not self._grants:
            return True  # no ACL configured → open

        p_lower = principal.lower()
        t_lower = target_fqn.lower()
        priv_lower = privilege.lower()

        principal_rules = self._grants.get(p_lower)
        if not principal_rules:
            return False

        for pattern, privileges in principal_rules.items():
            if priv_lower in privileges and fnmatch.fnmatchcase(t_lower, pattern):
                return True

        return False  # was implicitly None in old registry — fixed

    # ── Convenience registration wrappers ────────────────────────────────────
    # These keep existing callers working without changes.

    def get_or_create_schema(
        self, catalog_name: str, schema_name: str, description: str = ""
    ) -> "_SchemaStub":
        """Backward-compat stub — schemas are now implicit in the FQN."""
        return _SchemaStub(catalog_name, schema_name)

    def register_tool(
        self,
        tool: BaseTool,
        *,
        catalog: Optional[str] = None,
        schema: Optional[str] = None,
        category: Optional[str] = None,
        tags: Optional[List[str]] = None,
        aliases: Optional[List[str]] = None,
    ) -> "AgentCatalog":
        cat = catalog or self.default_catalog
        sch = schema or "default"
        spec = ResourceSpec(
            name=tool.name,
            namespace=f"{cat}.{sch}",
            resource_type=ResourceType.TOOL,
            description=getattr(tool, "description", ""),
            category=category or getattr(tool, "category", None) or "",
            tags=tags or getattr(tool, "tags", None) or [],
            aliases=aliases or getattr(tool, "aliases", None) or [],
        )
        fqn = spec.fqn
        if fqn in self._resources:
            raise ValueError(
                f"AgentCatalog: tool '{fqn}' is already registered. "
                "Use unregister() first if replacement is intended."
            )
        return self.register(spec, tool)

    def register_lazy_tool(
        self,
        name: str,
        factory_fn: Callable[[], BaseTool],
        *,
        description: str,
        catalog: Optional[str] = None,
        schema: Optional[str] = None,
        input_schema: Optional[Dict[str, Any]] = None,
        risk: ToolRisk = ToolRisk.SAFE,
        category: Optional[str] = None,
        tags: Optional[List[str]] = None,
        aliases: Optional[List[str]] = None,
    ) -> "AgentCatalog":
        lazy_tool = LazyTool(
            name=name,
            description=description,
            factory_fn=factory_fn,
            input_schema=input_schema,
            risk=risk,
            category=category,
            tags=tags,
            aliases=aliases,
        )
        return self.register_tool(
            lazy_tool,
            catalog=catalog,
            schema=schema,
            category=category,
            tags=tags,
            aliases=aliases,
        )

    def register_skill(
        self,
        skill_metadata: Any,
        *,
        catalog: Optional[str] = None,
        schema: Optional[str] = None,
    ) -> "AgentCatalog":
        cat = catalog or self.default_catalog
        sch = schema or "default"
        name = getattr(skill_metadata, "name", "")
        if not name:
            raise ValueError("Skill metadata must declare a name")
        spec = ResourceSpec(
            name=name,
            namespace=f"{cat}.{sch}",
            resource_type=ResourceType.SKILL,
            description=getattr(skill_metadata, "description", ""),
            category=getattr(skill_metadata, "category", None) or "",
            tags=getattr(skill_metadata, "tags", None) or [],
        )
        fqn = spec.fqn
        if fqn in self._resources:
            return self
        return self.register(spec, skill_metadata)

    def register_memory(
        self,
        name: str,
        memory: Any,
        *,
        catalog: Optional[str] = None,
        schema: Optional[str] = None,
    ) -> "AgentCatalog":
        """Register a memory instance. Raises on FQN collision (use ``unregister`` first to replace)."""
        cat = catalog or self.default_catalog
        sch = schema or "default"
        spec = ResourceSpec(
            name=name,
            namespace=f"{cat}.{sch}",
            resource_type=ResourceType.MEMORY,
        )
        return self.register(spec, memory)

    def register_context(
        self,
        name: str,
        context: Any,
        *,
        catalog: Optional[str] = None,
        schema: Optional[str] = None,
    ) -> "AgentCatalog":
        """Register a context strategy. Raises on FQN collision."""
        cat = catalog or self.default_catalog
        sch = schema or "default"
        spec = ResourceSpec(
            name=name,
            namespace=f"{cat}.{sch}",
            resource_type=ResourceType.CONTEXT,
        )
        return self.register(spec, context)

    def register_checkpoint_store(
        self,
        name: str,
        checkpoint_store: Any,
        *,
        catalog: Optional[str] = None,
        schema: Optional[str] = None,
    ) -> "AgentCatalog":
        """Register a checkpoint store. Raises on FQN collision."""
        cat = catalog or self.default_catalog
        sch = schema or "default"
        spec = ResourceSpec(
            name=name,
            namespace=f"{cat}.{sch}",
            resource_type=ResourceType.CHECKPOINT,
        )
        return self.register(spec, checkpoint_store)

    def register_model(
        self,
        name: str,
        model_client: Any,
        *,
        catalog: Optional[str] = None,
        schema: Optional[str] = None,
    ) -> "AgentCatalog":
        """Register an LLM model client. Raises on FQN collision."""
        cat = catalog or self.default_catalog
        sch = schema or "default"
        spec = ResourceSpec(
            name=name,
            namespace=f"{cat}.{sch}",
            resource_type=ResourceType.MODEL,
        )
        return self.register(spec, model_client)

    # ── SkillManager attachment ───────────────────────────────────────────────

    def init_skills(self, skill_manager: "SkillManagerProtocol") -> "AgentCatalog":
        """Attach a pre-constructed skill manager.

        The kernel does not know about the concrete ``SkillManager`` — callers
        in higher layers build it (typically from filesystem discovery) and
        inject it here. The manager must conform to ``SkillManagerProtocol``.
        """
        self.skill_manager = skill_manager
        return self

    # ── PromptEnricher protocol ───────────────────────────────────────────────

    def activate_skill(self, name: str) -> Optional[Any]:
        """Activate a skill via the embedded SkillManager (if configured)."""
        if self.skill_manager is not None:
            return self.skill_manager.activate(name)
        return None

    def active_context_block(self) -> str:
        """Active context XML block from the skill manager."""
        if self.skill_manager is not None:
            return self.skill_manager.active_context_block()
        return ""

    def available_skills_xml(self) -> str:
        """<available_skills> XML block for system-prompt injection."""
        skills = self.all_skills()
        if not skills:
            return ""
        lines = ["<available_skills>"]
        for meta in skills:
            location = str(
                getattr(meta, "skill_md_path", getattr(meta, "path", ""))
            ).replace("\\", "/")
            lines += [
                "  <skill>",
                f"    <name>{_xml_escape(meta.name)}</name>",
                f"    <description>{_xml_escape(meta.description)}</description>",
                f"    <location>{_xml_escape(location)}</location>",
                "  </skill>",
            ]
        lines.append("</available_skills>")
        return "\n".join(lines)

    def system_prompt_suffix(self) -> str:
        """Text to append to the system prompt (empty when no skills)."""
        xml = self.available_skills_xml()
        if not xml:
            return ""
        return (
            "\n\nYou have access to the following skills. "
            "When a task matches a skill's purpose, read the full SKILL.md "
            "at the listed location and follow its instructions precisely.\n\n" + xml
        )

    def inject_into_prompt(self, system_prompt: str) -> str:
        """Append skill context suffix to an existing system prompt."""
        suffix = self.system_prompt_suffix()
        return system_prompt.rstrip() + "\n" + suffix if suffix else system_prompt

    # ── Category browsing ─────────────────────────────────────────────────────

    def get_category(self, category_path: str) -> Optional[Any]:
        """Return a stub if any asset has a category matching ``category_path``."""
        path_lower = category_path.lower()
        for _, (spec, _) in self._resources.items():
            cat = spec.category.lower()
            if cat == path_lower or cat.startswith(path_lower + "/"):
                return _CategoryNode(category_path)
        return None

    def list_categories(self, parent_path: Optional[str] = None) -> List[Any]:
        """List distinct top-level (or child) categories across all resources."""
        seen: Set[str] = set()
        results: List[Any] = []
        for _, (spec, _) in self._resources.items():
            cat = spec.category
            if not cat:
                continue
            if parent_path:
                parent_lower = parent_path.lower()
                cat_lower = cat.lower()
                if not cat_lower.startswith(parent_lower + "/"):
                    continue
                remainder = cat_lower[len(parent_lower) + 1 :]
                child = remainder.split("/")[0]
                full = f"{parent_path}/{child}"
                if full.lower() not in seen:
                    seen.add(full.lower())
                    results.append(_CategoryNode(full))
            else:
                top = cat.split("/")[0].lower()
                if top not in seen:
                    seen.add(top)
                    results.append(_CategoryNode(top))
        return results

    def browse(self, category_path: str) -> List[Any]:
        """Return legacy asset views for resources matching ``category_path``."""
        path_lower = category_path.lower()
        return [
            _LegacyAssetView(spec, inst)
            for _, (spec, inst) in self._resources.items()
            if spec.category.lower() == path_lower
            or spec.category.lower().startswith(path_lower + "/")
        ]

    # ── Class constructors ────────────────────────────────────────────────────

    @classmethod
    def from_tools_and_skills(
        cls,
        tools: Iterable[BaseTool],
        skills: Optional[Iterable[Any]] = None,
    ) -> "AgentCatalog":
        """Build a catalog from existing tool/skill lists."""
        catalog = cls()
        for tool in tools:
            catalog.register_tool(tool)
        if skills:
            for skill_meta in skills:
                catalog.register_skill(skill_meta)
        return catalog

    # ── Internal helpers ─────────────────────────────────────────────────────

    def _by_type(self, *types: ResourceType) -> Iterable[tuple[ResourceSpec, Any]]:
        for fqn in self._insertion_order:
            spec, inst = self._resources[fqn]
            if spec.resource_type in types:
                yield spec, inst


# ── Backward-compat shim for CatalogAsset ────────────────────────────────────


class _LegacyAssetView:
    """Thin view that exposes the old CatalogAsset interface.

    Returned by ``get()``, ``get_asset()``, ``by_risk()``, ``search()``, so
    existing code that reads ``.tool``, ``.fqn``, ``.asset_type`` keeps working.
    """

    __slots__ = ("spec", "instance")

    def __init__(self, spec: ResourceSpec, instance: Any) -> None:
        self.spec = spec
        self.instance = instance

    @property
    def fqn(self) -> str:
        return self.spec.fqn

    @property
    def name(self) -> str:
        return self.spec.name

    @property
    def asset_type(self) -> str:
        return self.spec.resource_type.value

    @property
    def kind(self) -> str:
        return self.spec.resource_type.value

    @property
    def description(self) -> str:
        if isinstance(self.instance, BaseTool):
            return self.instance.description
        return self.spec.description

    @property
    def category(self) -> str:
        return self.spec.category

    @property
    def tags(self) -> List[str]:
        return list(self.spec.tags)

    @property
    def aliases(self) -> List[str]:
        return list(self.spec.aliases)

    @property
    def metadata(self) -> Dict[str, Any]:
        return {
            "category": self.spec.category,
            "tags": list(self.spec.tags),
            "aliases": list(self.spec.aliases),
        }

    # --- Typed accessors matching old CatalogAsset ---

    @property
    def tool(self) -> Optional[BaseTool]:
        return self.instance if isinstance(self.instance, BaseTool) else None

    @property
    def skill_metadata(self) -> Optional[Any]:
        return self.instance if self.spec.resource_type == ResourceType.SKILL else None

    @property
    def memory(self) -> Optional[Any]:
        return self.instance if self.spec.resource_type == ResourceType.MEMORY else None

    @property
    def context(self) -> Optional[Any]:
        return (
            self.instance if self.spec.resource_type == ResourceType.CONTEXT else None
        )

    @property
    def checkpoint_store(self) -> Optional[Any]:
        return (
            self.instance
            if self.spec.resource_type == ResourceType.CHECKPOINT
            else None
        )

    @property
    def model_client(self) -> Optional[Any]:
        return self.instance if self.spec.resource_type == ResourceType.MODEL else None


class _SchemaStub:
    """Backward-compat stub returned by get_or_create_schema()."""

    def __init__(self, catalog: str, schema: str) -> None:
        self.catalog_name = catalog
        self.name = schema


class _CategoryNode:
    """Minimal category node stub for backward compat."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.description = f"Category: {path}"


def _xml_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


# Backward-compat alias so existing
# ``from ravi.kernel.catalog.registry import AgentCatalogRegistry``
# calls can be migrated one file at a time.
AgentCatalogRegistry = AgentCatalog
