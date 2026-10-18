"""API, controller and workers authenticate each other with certificates when configured."""

from __future__ import annotations

import secrets
import threading
import time

import httpx
import pytest
import uvicorn
from pydantic import SecretStr

from conftest import free_port, wait_ready
from pki import issue, make_ca
from substrate_data.cli import listener_config
from substrate_data.compute.control_api import create_control_app
from substrate_data.compute.service import create_compute_app
from substrate_data.core.models import Settings
from substrate_data.core.tls import internal_client_context


class TlsServed:
    def __init__(self, app, settings: Settings):
        self.port = free_port()
        self.server = uvicorn.Server(listener_config(settings, app, "127.0.0.1", self.port))
        threading.Thread(target=self.server.run, daemon=True).start()
        deadline = time.monotonic() + 30
        while not self.server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        assert self.server.started

    @property
    def url(self) -> str:
        return f"https://127.0.0.1:{self.port}"

    def stop(self) -> None:
        self.server.should_exit = True


@pytest.mark.integration
def test_controller_attaches_and_probes_a_worker_over_mutual_tls(seaweed, ticket_keys, tmp_path):
    ca = make_ca(tmp_path)
    other = make_ca(tmp_path, "Other CA")
    server = issue(tmp_path, ca, "internal-server", ips=("127.0.0.1",))
    client = issue(tmp_path, ca, "internal-client", client=True)
    stranger = issue(tmp_path, other, "stranger", client=True)
    controller_token, worker_token = secrets.token_urlsafe(48), secrets.token_urlsafe(48)
    base = dict(
        _env_file=None,
        s3_endpoint_url=seaweed.s3,
        iceberg_catalog_url=seaweed.iceberg,
        s3_access_key_id=SecretStr(seaweed.access_key),
        s3_secret_access_key=SecretStr(seaweed.secret_key),
        controller_state=tmp_path / "compute",
        metadata=tmp_path / "c.sqlite",
        tls_cert_file=server[0],
        tls_key_file=server[1],
        tls_client_ca_file=ca[0],
        internal_ca_file=ca[0],
        internal_cert_file=client[0],
        internal_key_file=client[1],
    )
    controller = TlsServed(
        create_control_app(Settings(**base, controller_token=SecretStr(controller_token))),
        Settings(**base),
    )
    worker_settings = Settings(
        **base, worker_token=SecretStr(worker_token), ticket_public_key_file=ticket_keys[1]
    )
    worker = TlsServed(create_compute_app(worker_settings, "sql"), worker_settings)
    try:
        # A caller without a certificate from our CA cannot even reach the worker's health check.
        for cert in (None, stranger):
            context = internal_client_context(
                Settings(**{**base, "internal_cert_file": None, "internal_key_file": None})
            )
            if cert:
                context.load_cert_chain(*map(str, cert))
            with pytest.raises(httpx.HTTPError):
                httpx.get(worker.url + "/livez", verify=context)

        api_side = httpx.Client(
            base_url=controller.url + "/internal/v1",
            headers={"Authorization": "Bearer " + controller_token},
            verify=internal_client_context(Settings(**base)),
            timeout=30,
        )
        created = api_side.post(
            "/resources",
            json={"name": "tls-sql", "role": "sql", "url": worker.url, "token": worker_token},
        )
        assert created.status_code == 201, created.text
        # The controller's own health probe crossed an mTLS connection to reach "ready".
        wait_ready(api_side, created.json()["id"])
        api_side.close()
    finally:
        controller.stop()
        worker.stop()


def test_a_service_with_a_client_certificate_must_have_its_key():
    with pytest.raises(ValueError):
        Settings(_env_file=None, internal_cert_file="/x.crt")
