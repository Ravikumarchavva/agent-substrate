"""Webhook-based triggers — incoming HTTP requests fire workflows."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


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
    created_at: datetime = field(default_factory=datetime.utcnow)

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
    webhook path, the registry dispatches the configured workflow via Temporal.
    """

    def __init__(self) -> None:
        self._webhooks: dict[str, WebhookDef] = {}  # keyed by path
        self._temporal: Any = None

    def set_temporal(self, temporal: Any) -> None:
        """Inject TemporalClient for workflow dispatch."""
        self._temporal = temporal

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

        if self._temporal is None:
            return {"error": "TemporalClient not configured", "dispatched": False}

        try:
            # Merge incoming payload into target params
            params = {**webhook.target_params, "webhook_payload": payload}

            if webhook.target_type == "pipeline":
                wf_id = await self._temporal.start_pipeline_workflow(
                    webhook.target_name,
                    params.get("definition", {}),
                )
            elif webhook.target_type == "chain":
                wf_id = await self._temporal.start_chain_workflow(
                    params.get("code", ""),
                    params.get("description", ""),
                    timeout=params.get("timeout", 120),
                )
            else:
                return {
                    "error": f"Unknown target_type '{webhook.target_type}'",
                    "dispatched": False,
                }

            return {"dispatched": True, "workflow_id": wf_id}
        except Exception as exc:
            logger.exception("Webhook dispatch failed for '%s'", webhook.name)
            return {"error": str(exc), "dispatched": False}
