#!/usr/bin/env python3
"""
deploy.py — Cross-platform Kind cluster deploy script.

Replaces deployment/k8s/overlays/kind/deploy.ps1 and deploy.sh.
Requires: docker, kind, kubectl, uv on PATH. Python is already a project dep.

Usage:
    uv run python deploy.py
    uv run python deploy.py --cluster-name dev
    uv run python deploy.py --backend-tag my-backend:v2 --frontend-tag my-frontend:v2
"""

from __future__ import annotations

import argparse
import base64
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Terminal colour helpers (ANSI — work on Windows 10+ / Git Bash / Linux)
# ---------------------------------------------------------------------------
_ANSI = sys.stdout.isatty()


def _c(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _ANSI else text


def header(msg: str) -> None:
    print(_c(msg, "36"))  # cyan


def step(msg: str) -> None:
    print(_c(msg, "33"))  # yellow


def ok(msg: str) -> None:
    print(_c(msg, "32"))  # green


def gray(msg: str) -> None:
    print(_c(msg, "90"))  # dark gray


def fail(msg: str) -> None:
    print(_c(msg, "31"))  # red


# ---------------------------------------------------------------------------
# Subprocess helpers
# ---------------------------------------------------------------------------


def run(
    args: list[str], *, check: bool = True, capture: bool = False
) -> subprocess.CompletedProcess:
    return subprocess.run(args, check=check, capture_output=capture, text=True)


def run_pipe(producer: list[str], consumer: list[str]) -> None:
    """Run `producer | consumer` as two chained subprocesses."""
    p1 = subprocess.Popen(producer, stdout=subprocess.PIPE)
    p2 = subprocess.Popen(consumer, stdin=p1.stdout)
    p1.stdout.close()  # allow p1 to receive SIGPIPE if p2 exits
    p2.communicate()
    if p2.returncode != 0:
        raise subprocess.CalledProcessError(p2.returncode, consumer)


def require_command(cmd: str) -> None:
    if shutil.which(cmd) is None:
        fail(f"ERROR: '{cmd}' is required but not found on PATH")
        sys.exit(1)


# ---------------------------------------------------------------------------
# .env parser (simple key=value, ignores comments and blank lines)
# ---------------------------------------------------------------------------


def read_env_file(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, _, val = stripped.partition("=")
        env[key.strip()] = val.strip()
    return env


# ---------------------------------------------------------------------------
# Main deploy logic
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Deploy raavan microservices to a local Kind cluster.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--cluster-name", default="dev", help="Kind cluster name")
    parser.add_argument(
        "--backend-tag",
        default="agent-microservices-kind:local",
        help="Backend Docker image tag",
    )
    parser.add_argument(
        "--frontend-tag",
        default="chatbot-frontend-kind:local",
        help="Frontend Docker image tag",
    )
    args = parser.parse_args()

    cluster_name: str = args.cluster_name
    backend_tag: str = args.backend_tag
    frontend_tag: str = args.frontend_tag

    repo_root = Path(__file__).parent.resolve()
    k8s_root = repo_root / "deployment" / "k8s"
    kind_dir = k8s_root / "overlays" / "kind"
    frontend_dir = repo_root.parent / "ai-chatbot-ui"

    header("=== Microservices Kind Deploy ===")
    print(f"Cluster       : {cluster_name}")
    print(f"Backend image : {backend_tag}")
    print(f"Frontend image: {frontend_tag}")
    print(f"Repo root     : {repo_root}")
    print(f"Frontend dir  : {frontend_dir}")
    print()

    # ------------------------------------------------------------------
    # Step 0 — Prerequisites
    # ------------------------------------------------------------------
    step("Step 0: Checking prerequisites...")
    for cmd in ("docker", "kind", "kubectl", "uv"):
        require_command(cmd)

    clusters_result = run(["kind", "get", "clusters"], capture=True, check=False)
    if cluster_name not in (clusters_result.stdout or "").splitlines():
        fail(f"ERROR: Kind cluster '{cluster_name}' not found.")
        print(f"  Create it first:  kind create cluster --name {cluster_name}")
        sys.exit(1)

    run(["kubectl", "cluster-info", "--context", f"kind-{cluster_name}"], capture=True)
    ok(f"  kubectl context: kind-{cluster_name}")

    # ------------------------------------------------------------------
    # Step 1 — Read secrets from .env
    # ------------------------------------------------------------------
    print()
    step("Step 1: Reading secrets from .env...")
    env_file = repo_root / ".env"
    if not env_file.exists():
        fail(f"ERROR: No .env file at {env_file}")
        print("  Copy .env.example and fill in values.")
        sys.exit(1)

    env_vars = read_env_file(env_file)

    openai_key = env_vars.get("OPENAI_API_KEY", "")
    if not openai_key:
        fail("ERROR: OPENAI_API_KEY not found in .env")
        sys.exit(1)

    jwt_secret = base64.b64encode(secrets.token_bytes(32)).decode()

    enc_result = run(
        [
            sys.executable,
            "-c",
            "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())",
        ],
        capture=True,
        check=False,
    )
    encryption_key = (
        enc_result.stdout.strip()
        if enc_result.returncode == 0 and enc_result.stdout.strip()
        else "dev-only-encryption-key-change-me"
    )

    ok(f"  OPENAI_API_KEY  : {openai_key[:8]}...")
    ok(f"  JWT_SECRET      : {jwt_secret[:8]}...")

    # ------------------------------------------------------------------
    # Step 2 — Build Docker images
    # ------------------------------------------------------------------
    print()
    step("Step 2: Building Docker images...")

    print(f"  Building backend: {backend_tag}")
    run(
        [
            "docker",
            "build",
            "-t",
            backend_tag,
            "-f",
            str(repo_root / "deployment" / "docker" / "backend.Dockerfile"),
            str(repo_root),
        ]
    )

    if not frontend_dir.is_dir():
        fail(f"ERROR: Frontend directory not found at {frontend_dir}")
        sys.exit(1)

    print(f"  Building frontend: {frontend_tag}")
    run(
        [
            "docker",
            "build",
            "-t",
            frontend_tag,
            "--build-arg",
            "NEXT_PUBLIC_API_URL=",
            "-f",
            str(frontend_dir / "Dockerfile"),
            str(frontend_dir),
        ]
    )

    # ------------------------------------------------------------------
    # Step 3 — Load images into Kind
    # ------------------------------------------------------------------
    print()
    step("Step 3: Loading images into Kind cluster...")
    run(["kind", "load", "docker-image", backend_tag, "--name", cluster_name])
    run(["kind", "load", "docker-image", frontend_tag, "--name", cluster_name])
    ok("  Images loaded")

    # ------------------------------------------------------------------
    # Step 4 — Namespaces + infrastructure
    # ------------------------------------------------------------------
    print()
    step("Step 4: Deploying namespaces + infrastructure...")
    run(["kubectl", "apply", "-f", str(k8s_root / "base" / "namespaces.yaml")])
    run(["kubectl", "apply", "-f", str(kind_dir / "infra.yaml")])
    gray("  Waiting for Postgres...")
    run(
        [
            "kubectl",
            "rollout",
            "status",
            "statefulset/postgres",
            "-n",
            "af-data",
            "--timeout=180s",
        ]
    )
    gray("  Waiting for Redis...")
    run(
        [
            "kubectl",
            "rollout",
            "status",
            "deployment/redis",
            "-n",
            "af-data",
            "--timeout=60s",
        ]
    )

    # ------------------------------------------------------------------
    # Step 5 — Kubernetes secrets
    # ------------------------------------------------------------------
    print()
    step("Step 5: Creating secrets in all namespaces...")

    db_url = "postgresql+asyncpg://postgres:postgres@postgres.af-data.svc.cluster.local:5432/agentdb"
    redis_url = "redis://redis.af-data.svc.cluster.local:6379/0"

    def create_secret(namespace: str, secret_name: str) -> None:
        run_pipe(
            producer=[
                "kubectl",
                "create",
                "secret",
                "generic",
                secret_name,
                f"--namespace={namespace}",
                f"--from-literal=DATABASE_URL={db_url}",
                f"--from-literal=REDIS_URL={redis_url}",
                f"--from-literal=JWT_SECRET={jwt_secret}",
                f"--from-literal=OPENAI_API_KEY={openai_key}",
                f"--from-literal=ENCRYPTION_KEY={encryption_key}",
                "--dry-run=client",
                "-o",
                "yaml",
            ],
            consumer=["kubectl", "apply", "-f", "-"],
        )
        ok(f"  {secret_name} in {namespace}")

    create_secret("af-edge", "shared-secrets")
    create_secret("af-platform", "platform-secrets")
    create_secret("af-runtime", "runtime-secrets")

    google_client_id = env_vars.get("GOOGLE_CLIENT_ID", "")
    google_client_secret = env_vars.get("GOOGLE_CLIENT_SECRET", "")
    spotify_client_id = env_vars.get("SPOTIFY_CLIENT_ID", "")
    spotify_client_secret = env_vars.get("SPOTIFY_CLIENT_SECRET", "")

    if google_client_id or spotify_client_id:
        run_pipe(
            producer=[
                "kubectl",
                "create",
                "secret",
                "generic",
                "frontend-oauth-secrets",
                "--namespace=af-edge",
                f"--from-literal=GOOGLE_CLIENT_ID={google_client_id}",
                f"--from-literal=GOOGLE_CLIENT_SECRET={google_client_secret}",
                f"--from-literal=SPOTIFY_CLIENT_ID={spotify_client_id}",
                f"--from-literal=SPOTIFY_CLIENT_SECRET={spotify_client_secret}",
                "--dry-run=client",
                "-o",
                "yaml",
            ],
            consumer=["kubectl", "apply", "-f", "-"],
        )
        ok("  frontend-oauth-secrets in af-edge")

    # ------------------------------------------------------------------
    # Step 6 — Kustomize apply
    # ------------------------------------------------------------------
    print()
    step("Step 6: Deploying all services via kustomize...")
    run(["kubectl", "apply", "-k", str(kind_dir)])
    ok("  Kustomize apply complete")

    # ------------------------------------------------------------------
    # Step 7 — Rollout restart
    # ------------------------------------------------------------------
    print()
    step("Step 7: Restarting deployments for fresh images...")

    deployments: list[tuple[str, str]] = [
        ("af-edge", "gateway-bff"),
        ("af-edge", "frontend"),
        ("af-platform", "identity-auth"),
        ("af-platform", "policy-authorization"),
        ("af-runtime", "conversation"),
        ("af-runtime", "job-controller"),
        ("af-runtime", "agent-runtime"),
        ("af-runtime", "tool-executor"),
        ("af-runtime", "human-gate"),
        ("af-runtime", "live-stream"),
        ("af-runtime", "file-store"),
        ("af-runtime", "admin"),
    ]

    for ns, name in deployments:
        run(
            ["kubectl", "rollout", "restart", f"deployment/{name}", "-n", ns],
            check=False,
            capture=True,
        )

    # ------------------------------------------------------------------
    # Step 8 — Wait for ready
    # ------------------------------------------------------------------
    print()
    step("Step 8: Waiting for all deployments to be ready...")

    failed: list[str] = []
    for ns, name in deployments:
        print(f"  Waiting: {name} ({ns})...", end="", flush=True)
        r = run(
            [
                "kubectl",
                "rollout",
                "status",
                f"deployment/{name}",
                "-n",
                ns,
                "--timeout=180s",
            ],
            check=False,
            capture=True,
        )
        if r.returncode != 0:
            print(_c(" FAILED", "31"))
            failed.append(f"{ns}/{name}")
        else:
            print(_c(" OK", "32"))

    if failed:
        print()
        fail("=== WARNING: These deployments failed to become ready ===")
        for item in failed:
            fail(f"  - {item}")
        print("  Check logs: kubectl logs -n <namespace> deployment/<name>")

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    print()
    header("=== Deployment Summary ===")
    print("Namespaces:")
    r = run(
        [
            "kubectl",
            "get",
            "ns",
            "-l",
            "app.kubernetes.io/part-of=raavan",
            "-o",
            "custom-columns=NAME:.metadata.name",
            "--no-headers",
        ],
        check=False,
        capture=True,
    )
    for line in (r.stdout or "").splitlines():
        if line.strip():
            print(f"  {line.strip()}")

    print("\nAll pods:")
    for ns in ("af-data", "af-edge", "af-platform", "af-runtime", "af-observability"):
        r = run(
            ["kubectl", "get", "pods", "-n", ns, "--no-headers"],
            check=False,
            capture=True,
        )
        pods = (r.stdout or "").strip()
        if pods:
            print(_c(f"  [{ns}]", "33"))
            for line in pods.splitlines():
                if line.strip():
                    print(f"    {line}")

    print()
    header("=== Access ===")
    print("  Frontend     : http://localhost/")
    print("  Gateway API  : http://localhost/chat, /threads, /stream, ...")
    print("  Grafana      : http://localhost/grafana/")
    print("  Health check : http://localhost/health")
    print()
    ok("=== Done ===")


if __name__ == "__main__":
    main()
