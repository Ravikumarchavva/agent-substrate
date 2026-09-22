"""Lazy kubernetes-client config loading, shared by per-user SandboxTemplate management.

Separate from ``k8s_agent_sandbox.k8s_helper.K8sHelper`` because that SDK
helper only targets the ``agents.x-k8s.io`` SandboxClaim CRD group; the
SandboxTemplate CRD (used here to generate per-user templates) lives under
``extensions.agents.x-k8s.io`` and has no SDK wrapper.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kubernetes.client import CustomObjectsApi

_api: "CustomObjectsApi | None" = None


def get_custom_objects_api() -> "CustomObjectsApi":
    """Return a process-wide ``CustomObjectsApi``, loading kube config once.

    Tries in-cluster config first (the normal case inside a deployed pod),
    falling back to the local kubeconfig for dev/testing against a real or
    kind cluster — same fallback order as the vendored SDK's own K8sHelper.
    """
    global _api
    if _api is not None:
        return _api

    from kubernetes import client, config

    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()

    _api = client.CustomObjectsApi()
    return _api
