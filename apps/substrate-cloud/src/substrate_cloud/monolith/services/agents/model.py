"""The model an agent thinks with: its own when it names one, else the deployment's."""

from __future__ import annotations

from typing import Any

from substrate.integrations.llm.factory import (
    CHAT_MODEL_FALLBACKS,
    create_model_client,
    detect_provider,
    has_provider_api_key,
    model_supports_vision,
    resolve_model_for_available_credentials,
    strip_provider_prefix,
)


def has_credentials(deps: Any, model: str) -> bool:
    """Whether the deployment can call ``model``: a key is configured for its provider."""
    return has_provider_api_key(detect_provider(model), getattr(deps, "api_keys", {}))


def client_for(deps: Any, model: str | None) -> Any:
    """The chat client to run an agent with. ``None`` (no model of its own) is the deployment's client, and so is a model it already runs."""
    if not model:
        return deps.model_client
    keys = getattr(deps, "api_keys", {})
    resolved = resolve_model_for_available_credentials(
        model, api_keys=keys, fallback_models=(getattr(deps, "chat_model", ""), *CHAT_MODEL_FALLBACKS)
    )
    current = deps.model_client
    if getattr(current, "provider", None) == detect_provider(resolved) and getattr(current, "model", None) == strip_provider_prefix(resolved):
        return current
    return create_model_client(resolved, api_keys=keys, **getattr(deps, "model_client_kwargs", {}))


def sees(model: str | None) -> bool | None:
    """Whether the agent's own model reads pictures, or ``None`` when it uses the deployment's (which is not known where this is asked)."""
    return None if not model else model_supports_vision(model)
