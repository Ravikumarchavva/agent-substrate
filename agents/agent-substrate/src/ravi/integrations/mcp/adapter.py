"""MCPCatalogAdapter — registers MCP server tools into AgentCatalog."""

from __future__ import annotations

from ravi.kernel.agent_catalog import AgentCatalog, ResourceSpec, ResourceType
from ravi.integrations.mcp.client import MCPClient
from ravi.integrations.mcp.tool import MCPTool


class MCPCatalogAdapter:
    """Bridges an MCPClient and AgentCatalog.

    Discovers all tools exposed by a connected MCPClient and registers them
    under ResourceType.MCP_TOOL so the agent runtime can look them up via the
    catalog the same way it handles native tools.

    Example::

        catalog = AgentCatalog()
        adapter = MCPCatalogAdapter(catalog, namespace="my_server")
        await adapter.register(mcp_client)
        # All tools now visible via catalog.get_tool(name)
    """

    def __init__(self, catalog: AgentCatalog, namespace: str = "mcp") -> None:
        self._catalog = catalog
        self._namespace = namespace

    async def register(self, client: MCPClient) -> list[str]:
        """Discover all tools from *client* and register them in the catalog.

        Returns the FQNs of every newly-registered tool.
        """
        tools = await MCPTool.from_mcp_client(client)
        fqns: list[str] = []
        for tool in tools:
            spec = ResourceSpec(
                name=tool.name,
                namespace=self._namespace,
                resource_type=ResourceType.MCP_TOOL,
                description=tool.description or "",
            )
            self._catalog.register(spec, tool)
            fqns.append(spec.fqn)
        return fqns
