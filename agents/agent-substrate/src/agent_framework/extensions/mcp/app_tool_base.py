"""Base class for MCP tools that render interactive UI panels.

Every app tool must declare a ``ui_resource_uri`` class variable. The
``McpAppTool.__init__`` helper injects the corresponding
``_meta.ui.resourceUri`` field automatically, eliminating the boilerplate
``_meta`` dict from every subclass.

Usage::

    class DataVisualizerTool(McpAppTool):
        ui_resource_uri: ClassVar[str] = "ui://data_visualizer"
        risk: ClassVar[ToolRisk] = ToolRisk.SAFE

        def __init__(self) -> None:
            super().__init__(
                name="data_visualizer",
                description="...",
                input_schema={...},
                annotations={...},
            )

        async def execute(self, **kwargs: Any) -> ToolResult:
            ...
"""

from __future__ import annotations

from typing import Any, ClassVar

from agent_framework.core.tools.base_tool import BaseTool


class McpAppTool(BaseTool):
    """Abstract base for UI-rich MCP App tools.

    Subclasses **must** set ``ui_resource_uri`` as a class variable
    (e.g. ``"ui://data_visualizer"``).  The ``__init__`` helper builds
    the ``_meta`` dict automatically, so subclasses only need to pass
    the regular ``name``, ``description``, ``input_schema``, and
    optionally ``annotations``.
    """

    # Subclasses MUST override this, e.g. "ui://data_visualizer"
    ui_resource_uri: ClassVar[str]

    def __init__(
        self,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        annotations: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        if not hasattr(self.__class__, "ui_resource_uri"):
            raise TypeError(
                f"{self.__class__.__name__} must define a 'ui_resource_uri' class variable."
            )
        super().__init__(
            name=name,
            description=description,
            input_schema=input_schema,
            annotations=annotations,
            _meta={"ui": {"resourceUri": self.__class__.ui_resource_uri}},
            **kwargs,
        )
