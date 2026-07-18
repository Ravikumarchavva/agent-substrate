"""CodeInterpreterService._ensure_user_template — per-user SandboxTemplate spec
construction: correct subPath/mountPath/env, name hashing, and idempotency."""

from __future__ import annotations

import hashlib
from typing import Any

import pytest
from kubernetes.client.rest import ApiException

from substrate.capabilities.tools.code_interpreter.code_interpreter.sandbox_service import (
    CodeInterpreterConfig,
    CodeInterpreterService,
)
from substrate.capabilities.tools.code_interpreter.code_interpreter.session_store import (
    InMemorySessionStore,
)

_BASE_TEMPLATE = {
    "metadata": {"name": "python-sandbox-template", "namespace": "af-runtime"},
    "spec": {
        "podTemplate": {
            "spec": {
                "containers": [
                    {
                        "name": "code-interpreter-runtime",
                        "image": "code-interpreter:latest",
                    }
                ],
                "restartPolicy": "OnFailure",
            }
        }
    },
}


class _FakeCustomObjectsApi:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.existing: dict[str, dict[str, Any]] = {
            "python-sandbox-template": _BASE_TEMPLATE
        }

    def get_namespaced_custom_object(self, group, version, namespace, plural, name):
        row = self.existing.get(name)
        if row is None:
            raise ApiException(status=404)
        return row

    def create_namespaced_custom_object(self, group, version, namespace, plural, body):
        if body["metadata"]["name"] in self.existing:
            raise ApiException(status=409)
        self.created.append(body)
        self.existing[body["metadata"]["name"]] = body


@pytest.fixture
def service(monkeypatch) -> tuple[CodeInterpreterService, _FakeCustomObjectsApi]:
    fake_api = _FakeCustomObjectsApi()
    monkeypatch.setattr(
        "substrate.capabilities.tools.code_interpreter.code_interpreter.k8s_helper.get_custom_objects_api",
        lambda: fake_api,
    )
    config = CodeInterpreterConfig(
        template="python-sandbox-template",
        namespace="af-runtime",
        workspace_pvc_claim="user-workspaces",
        workspace_mount_path="/app/workspace",
    )
    svc = CodeInterpreterService(
        config=config, store=InMemorySessionStore(), client=object()
    )
    return svc, fake_api


def test_ensure_user_template_creates_volume_and_mount(service) -> None:
    svc, fake_api = service
    name = svc._ensure_user_template("user-42")

    assert name.startswith("python-sandbox-")
    assert len(fake_api.created) == 1
    body = fake_api.created[0]
    pod_spec = body["spec"]["podTemplate"]["spec"]

    [volume] = pod_spec["volumes"]
    assert volume["persistentVolumeClaim"]["claimName"] == "user-workspaces"

    [container] = pod_spec["containers"]
    [mount] = container["volumeMounts"]
    assert mount["mountPath"] == "/app/workspace"
    assert mount["subPath"] == "users/user-42"

    env_names = {e["name"] for e in container["env"]}
    assert "CODE_INTERPRETER_WORKSPACE" in env_names
    workspace_env = next(
        e for e in container["env"] if e["name"] == "CODE_INTERPRETER_WORKSPACE"
    )
    assert workspace_env["value"] == "/app/workspace"


def test_ensure_user_template_name_is_stable_and_deterministic(service) -> None:
    svc, _fake_api = service
    name_1 = svc._ensure_user_template("user-42")
    name_2 = svc._ensure_user_template("user-42")
    assert name_1 == name_2


def test_ensure_user_template_different_users_get_different_names(service) -> None:
    svc, _fake_api = service
    name_a = svc._ensure_user_template("user-a")
    name_b = svc._ensure_user_template("user-b")
    assert name_a != name_b


def test_ensure_user_template_idempotent_against_concurrent_create(service) -> None:
    """A 409 from create (another request won the race) must not raise."""
    svc, fake_api = service
    # Pre-create the target template out-of-band to simulate the race.
    name = f"python-sandbox-{hashlib.sha256(b'user-42').hexdigest()[:20]}"
    fake_api.existing[name] = {"metadata": {"name": name}, "spec": {}}

    result = svc._ensure_user_template("user-42")
    assert result == name


def test_ensure_user_template_missing_base_template_raises(service) -> None:
    svc, fake_api = service
    fake_api.existing.pop("python-sandbox-template")
    with pytest.raises(RuntimeError, match="not found"):
        svc._ensure_user_template("user-42")
