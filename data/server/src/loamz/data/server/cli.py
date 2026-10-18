"""loamz-data: run the control plane, the compute controller, a worker role, or attach compute.

loamz-data serve [--roles catalog,sql,...]     the API (every feature, or the roles named)
loamz-data controller                          the compute controller
loamz-data worker --role sql|jobs|documents    attached compute
loamz-data worker --role inference             the embedding and reranking service
loamz-data attach --name ... --url ... --token-env ...
"""

from __future__ import annotations

import argparse
import logging
import os
import ssl

import httpx
import uvicorn

from loamz.data.server.kernel.settings import LOOPBACK, Settings
from loamz.data.server.kernel.tls import internal_client_context


def attach(settings: Settings, args: argparse.Namespace) -> None:
    """Register already-running compute with the controller. Nothing is started here."""
    token = os.environ.get(args.token_env or "")
    if not (args.name and args.url and token):
        raise SystemExit("attach requires --name, --url and --token-env naming a set variable")
    client = httpx.Client(
        base_url=settings.controller_url + "/internal/v1",
        headers={"Authorization": "Bearer " + settings.controller_token.get_secret_value()},
        timeout=10,
        trust_env=False,
        verify=internal_client_context(settings),
    )
    existing = client.get("/resources")
    existing.raise_for_status()
    if any(r["name"] == args.name for r in existing.json()["resources"]):
        print(f"{args.name} is already attached")
        return
    response = client.post(
        "/resources",
        json={"name": args.name, "role": args.role, "url": args.url, "token": token},
    )
    if response.status_code != 201:
        raise SystemExit(f"Attach failed: {response.json().get('detail', response.status_code)}")
    print(f"Attached {args.name} ({args.role}) at {args.url}")


def listener_config(settings: Settings, app: object, host: str, port: int) -> uvicorn.Config:
    """The API listener: TLS from files when configured, never plaintext on a routable address
    unless the operator states that a proxy or mesh terminates TLS in front."""
    secured = settings.tls_cert_file is not None
    if not secured and host not in LOOPBACK and not settings.behind_tls_proxy:
        raise SystemExit(
            f"Refusing to serve plaintext HTTP on {host}. Set LOAMZ_DATA_TLS_CERT_FILE and "
            "LOAMZ_DATA_TLS_KEY_FILE, or LOAMZ_DATA_BEHIND_TLS_PROXY=true if a trusted "
            "proxy or service mesh terminates TLS in front of this process."
        )
    config = uvicorn.Config(
        app,  # type: ignore[arg-type]
        host=host,
        port=port,
        log_level="info",
        access_log=False,
        ssl_certfile=str(settings.tls_cert_file) if secured else None,
        ssl_keyfile=str(settings.tls_key_file) if secured else None,
        ssl_ca_certs=str(settings.tls_client_ca_file) if settings.tls_client_ca_file else None,
        ssl_cert_reqs=ssl.CERT_REQUIRED if settings.tls_client_ca_file else ssl.CERT_NONE,
    )
    if secured:
        config.load()
        assert config.ssl is not None
        config.ssl.minimum_version = (
            ssl.TLSVersion.TLSv1_3 if settings.tls_min_version == "1.3" else ssl.TLSVersion.TLSv1_2
        )
        if settings.tls_min_version == "1.2":  # TLS 1.2 only with forward-secret AEAD suites
            config.ssl.set_ciphers("ECDHE+AESGCM:ECDHE+CHACHA20")
    return config


def serve_api(app: object, settings: Settings, host: str, port: int) -> None:
    uvicorn.Server(listener_config(settings, app, host, port)).run()


def main() -> None:
    parser = argparse.ArgumentParser(description="Loamz Data")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("command", choices=["serve", "controller", "worker", "attach"])
    parser.add_argument(
        "--host", help="bind address (workers default to LOAMZ_DATA_WORKER_BIND_HOST)"
    )
    parser.add_argument("--port", type=int)
    parser.add_argument("--role", choices=["sql", "jobs", "documents", "inference"], default="sql")
    parser.add_argument("--roles", help="serve: comma-separated feature roles (default: all)")
    parser.add_argument("--name", help="attach: resource name")
    parser.add_argument("--url", help="attach: address of the running compute")
    parser.add_argument("--token-env", help="attach: environment variable holding its token")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = Settings(_env_file=args.env_file)
    if args.command == "attach":
        attach(settings, args)
        return
    if args.command == "controller":
        from loamz.data.server.features.compute.control_api import create_control_app

        app, port = create_control_app(settings), args.port or 8014
    elif args.command == "worker" and args.role == "inference":
        from loamz.data.server.features.inference.app import create_inference_app
        from loamz.data.server.kernel.container import Container

        app, port = create_inference_app(Container(settings)), args.port or 8030
        args.host = args.host or settings.worker_bind_host
    elif args.command == "worker":
        from loamz.data.server.features.compute.worker import create_compute_app

        app, port = create_compute_app(settings, args.role), args.port or 8011
        args.host = args.host or settings.worker_bind_host
    else:
        from loamz.data.server.app import create_app
        from loamz.data.server.kernel.container import Container

        roles = set(args.roles.split(",")) if args.roles else None
        app, port = create_app(Container(settings), roles=roles), args.port or 8020
    host = args.host or "127.0.0.1"
    serve_api(app, settings, host, port)


if __name__ == "__main__":
    main()
