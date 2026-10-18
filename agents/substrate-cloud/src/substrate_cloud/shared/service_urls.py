"""Where the microservices find each other (ports are the registry in deploy/PORTS.md)."""

from __future__ import annotations

import os

from substrate_cloud.shared.settings import settings

PORTS: dict[str, int] = {
    "gateway": 8001,
    "policy": 8011,
    "conversation": 8012,
    "job_controller": 8013,
    "agent_runtime": 8014,
    "tool_executor": 8015,
    "human_gate": 8016,
    "live_stream": 8017,
    "admin": 8019,
}


def service_url(name: str) -> str:
    """The base URL of one service: ``<NAME>_SERVICE_URL`` if set, else ``SERVICE_URL_TEMPLATE`` filled in."""
    if name not in PORTS:
        raise KeyError(f"unknown service {name!r}")
    override = os.environ.get(f"{name.upper()}_SERVICE_URL")
    if override:
        return override.rstrip("/")
    return settings.SERVICE_URL_TEMPLATE.format(
        name=name.replace("_", "-"), port=PORTS[name]
    ).rstrip("/")
