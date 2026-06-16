"""Webhook-based triggers — incoming HTTP requests fire workflows."""

from __future__ import annotations
from ravi.logger import setup_logging

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ravi.agents.runtime import Runtime

logger = setup_logging()


@dataclass
class WebhookDef:
    """Definition of a webhook trigger."""

    name: str
    path: str  # URL path segment (e.g., "deploy-notify")
    target_type: str  # "pipeline" | "chain"
    target_name: str
    target_params: dict[str, Any] = field(default_factory=dict)
    secret: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    enabled: bool = True
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))

    @property
    def url_path(self) -> str:
        return f"/webhooks/{self.path}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "url_path": self.url_path,
            "target_type": self.target_type,
            "target_name": self.target_name,
            "target_params": self.target_params,
            "secret": self.secret,
            "enabled": self.enabled,
            "created_at": self.created_at.isoformat(),
        }


class WebhookRegistry:
    """Registry for webhook-triggered workflows.

    Webhooks are registered dynamically. When an HTTP POST arrives at the
    webhook path, the registry dispatches the configured workflow via native Runtime.
    """

    def __init__(self, runtime: Runtime | None = None) -> None:
        self._webhooks: dict[str, WebhookDef] = {}  # keyed by path
        self._runtime = runtime

    def set_runtime(self, runtime: Runtime) -> None:
        """Inject active Runtime for trigger dispatch."""
        self._runtime = runtime

    async def register(
        self,
        name: str,
        path: str,
        target_type: str,
        target_name: str,
        target_params: dict[str, Any] | None = None,
    ) -> WebhookDef:
        """Register a new webhook."""
        webhook = WebhookDef(
            name=name,
            path=path,
            target_type=target_type,
            target_name=target_name,
            target_params=target_params or {},
        )
        self._webhooks[path] = webhook
        logger.info("Registered webhook '%s' at %s", name, webhook.url_path)
        return webhook

    async def unregister(self, path: str) -> bool:
        """Unregister a webhook by path."""
        if path not in self._webhooks:
            return False
        del self._webhooks[path]
        logger.info("Unregistered webhook at /webhooks/%s", path)
        return True

    def list_webhooks(self) -> list[WebhookDef]:
        """Return all registered webhooks."""
        return list(self._webhooks.values())

    def get_webhook(self, path: str) -> WebhookDef | None:
        """Get a webhook by path."""
        return self._webhooks.get(path)

    async def handle(
        self, path: str, payload: dict[str, Any], secret: str | None = None
    ) -> dict[str, Any]:
        """Handle an incoming webhook request.

        Returns dispatch result dict.
        """
        webhook = self._webhooks.get(path)
        if webhook is None:
            return {
                "error": f"No webhook registered at /webhooks/{path}",
                "dispatched": False,
            }

        if not webhook.enabled:
            return {"error": "Webhook is disabled", "dispatched": False}

        # Validate secret if provided
        if secret and secret != webhook.secret:
            return {"error": "Invalid webhook secret", "dispatched": False}

        logger.info(
            "Webhook '%s' triggered → %s:%s",
            webhook.name,
            webhook.target_type,
            webhook.target_name,
        )

        if self._runtime is not None:
            from ravi.kernel.core.identity import AgentId
            from ravi.kernel.messaging.message import Message, DataPayload

            combined_params = {**webhook.target_params, **payload}
            agent_id = AgentId(type=webhook.target_type, key=webhook.target_name)
            msg = Message(
                target=agent_id,
                payload=DataPayload(data=combined_params),
            )
            try:
                run_id = await self._runtime.submit(agent_id, msg)
                logger.info(
                    "Webhook '%s' submitted run %s to native runtime for %s",
                    webhook.name,
                    run_id,
                    agent_id,
                )
                return {
                    "status": "triggered",
                    "dispatched": True,
                    "run_id": run_id,
                    "target_type": webhook.target_type,
                    "target_name": webhook.target_name,
                }
            except Exception as exc:
                logger.error(
                    "Webhook '%s' failed to submit run for %s: %s",
                    webhook.name,
                    agent_id,
                    exc,
                )
                return {
                    "status": "failed",
                    "dispatched": False,
                    "error": str(exc),
                }
        else:
            logger.warning(
                "Webhook '%s' triggered, but no Runtime is configured for dispatch.",
                webhook.name,
            )
            return {"status": "triggered", "dispatched": False}
