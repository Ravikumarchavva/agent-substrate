from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional
from ravi.core.tools.base_tool import BaseTool, ToolResult, ToolRisk, HitlMode, ToolAnnotations


class LazyTool(BaseTool):
    """Wrapper that defers loading of a real BaseTool until first execution/evaluation.

    This helps keep the AgentCatalogRegistry extremely lightweight at startup and
    during testing, avoiding importing heavy packages (like kubernetes, google-api, etc.)
    or setting up client instances eagerly.
    """

    def __init__(
        self,
        name: str,
        description: str,
        factory_fn: Callable[[], BaseTool],
        *,
        input_schema: Optional[Dict[str, Any]] = None,
        annotations: Optional[ToolAnnotations | Dict[str, Any]] = None,
        risk: ToolRisk = ToolRisk.SAFE,
        hitl_mode: HitlMode = HitlMode.BLOCKING,
        hitl_timeout_seconds: Optional[float] = None,
        category: Optional[str] = None,
        tags: Optional[List[str]] = None,
        aliases: Optional[List[str]] = None,
    ) -> None:
        super().__init__(
            name=name,
            description=description,
            input_schema=input_schema,
            annotations=annotations,
            risk=risk,
            hitl_mode=hitl_mode,
            hitl_timeout_seconds=hitl_timeout_seconds,
            category=category,
            tags=tags,
            aliases=aliases,
        )
        self._factory_fn = factory_fn
        self._resolved_instance: Optional[BaseTool] = None

    def resolve(self) -> BaseTool:
        """Resolve and instantiate the underlying concrete tool."""
        if self._resolved_instance is None:
            self._resolved_instance = self._factory_fn()
            # Sync key fields from lazy metadata if needed
            self._resolved_instance.category = self.category or self._resolved_instance.category
            self._resolved_instance.tags = self.tags or self._resolved_instance.tags
            self._resolved_instance.aliases = self.aliases or self._resolved_instance.aliases
        return self._resolved_instance

    async def execute(self, **kwargs: Any) -> ToolResult:
        """Resolve the real tool and execute it."""
        tool = self.resolve()
        return await tool.execute(**kwargs)
