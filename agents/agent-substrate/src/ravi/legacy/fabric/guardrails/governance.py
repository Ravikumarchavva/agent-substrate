"""Governance Access Control middleware.

Enforces SQL-style grants policy on tool execution based on the principal
agent's name and the target tool's fully qualified name.
"""

from __future__ import annotations
from ravi.logger import setup_logging

from typing import Any

from ravi.kernel.middleware.base import (
    BaseMiddleware,
    MiddlewareContext,
    MiddlewareStage,
)
from ravi.exceptions import GuardrailTripwireError

logger = setup_logging()


class GovernanceMiddleware(BaseMiddleware):
    """Enforces Unity Catalog grants and permissions.

    Verifies that the executing agent (principal) has the 'execute' privilege
    on the target tool.
    """

    def __init__(
        self,
        *,
        name: str = "governance",
        catalog: Any,  # The AgentCatalogRegistry instance
    ) -> None:
        super().__init__(name)
        self.catalog = catalog

    async def before(self, ctx: MiddlewareContext) -> MiddlewareContext:
        if ctx.stage != MiddlewareStage.TOOL_EXECUTION:
            return ctx

        tool_name = ctx.tool_name
        if not tool_name:
            return ctx

        # Resolve FQN for the tool
        # Default search path or we can use default/system schemas
        search_path = ctx.metadata.get("search_path", ["default", "system"])
        fqn = self.catalog.resolve_fqn(tool_name, search_path)

        if not fqn:
            # If not found in catalog, but registered as direct tool, we can form FQN
            fqn = f"{self.catalog.default_catalog}.default.{tool_name}".lower()

        principal = ctx.agent_name or "default_agent"

        # Check permissions
        is_permitted = self.catalog.check_permission(principal, fqn, "execute")
        if not is_permitted:
            msg = f"Agent '{principal}' is not permitted to execute tool '{fqn}'"
            logger.warning("Governance blocked execution: %s", msg)
            raise GuardrailTripwireError(
                message=msg,
                guardrail_name="governance_policy",
                details={
                    "principal": principal,
                    "target_fqn": fqn,
                    "action": "execute",
                    "guardrail_type": "tool_call",
                    "result": {
                        "guardrail_type": "tool_call",
                        "guardrail_name": "governance_policy",
                        "status": "tripped",
                    },
                },
            )

        return ctx

    async def after(self, ctx: MiddlewareContext, result: Any) -> Any:
        return result
