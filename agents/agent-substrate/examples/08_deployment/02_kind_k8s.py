"""Example 08-2: Kind cluster verification for ravi-engine deployment.

Checks that a Kind (Kubernetes in Docker) cluster is ready to receive ravi-engine
manifests. This is an informational/verification script — it does not apply any
manifests itself.

Actual Kubernetes manifests live in: deployment/k8s/

Steps verified:
  1. kubectl is available on PATH
  2. Current context is a Kind cluster
  3. Required namespaces exist
  4. Expected deployments are present
  5. Prints the command to apply ravi-engine manifests
"""

import asyncio
import subprocess
import sys
from pathlib import Path

# Infrastructure: requires a running Kind cluster.
#   Create one:  kind create cluster --name ai-lab
#   Deploy:      kubectl apply -k deployment/k8s/overlays/local

REQUIRED_NAMESPACES = ["agent-framework"]
REQUIRED_DEPLOYMENTS = [
    ("agent-framework", "agent-backend"),
]
MANIFESTS_DIR = Path(__file__).parent.parent.parent / "deployment" / "k8s"

# ---


def run_cmd(*args: str, check: bool = False) -> tuple[int, str]:
    """Run a subprocess command; return (returncode, combined output)."""
    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode, (result.stdout + result.stderr).strip()


# ---


def section_1_kubectl() -> bool:
    """Section 1 — Verify kubectl is on PATH."""
    print("=== Section 1: kubectl availability ===")

    rc, out = run_cmd("kubectl", "version", "--client", "--short")
    if rc == 0:
        print(f"  ✓  kubectl found: {out.splitlines()[0] if out else 'ok'}")
        return True

    # Older kubectl drops --short; try without
    rc2, out2 = run_cmd("kubectl", "version", "--client")
    if rc2 == 0:
        version_line = next(
            (l for l in out2.splitlines() if "Client" in l), out2.splitlines()[0]
        )
        print(f"  ✓  kubectl found: {version_line}")
        return True

    print("  ✗  kubectl not found on PATH")
    print("     Install: https://kubernetes.io/docs/tasks/tools/")
    return False


# ---


def section_2_kind_context() -> bool:
    """Section 2 — Verify the active context is a Kind cluster."""
    print("\n=== Section 2: Kind cluster context ===")

    rc, ctx = run_cmd("kubectl", "config", "current-context")
    if rc != 0:
        print(f"  ✗  No active kubeconfig context: {ctx}")
        return False

    print(f"  Active context: {ctx}")
    if not ctx.startswith("kind-"):
        print("  ⚠  Context does not look like a Kind cluster (expected 'kind-*')")
        print("     Create one: kind create cluster --name ai-lab")
        return False

    cluster_name = ctx.removeprefix("kind-")
    rc2, nodes_out = run_cmd("kubectl", "get", "nodes", "--no-headers")
    if rc2 != 0:
        print(f"  ✗  Cannot reach cluster: {nodes_out[:120]}")
        return False

    node_lines = [l for l in nodes_out.splitlines() if l.strip()]
    print(f"  ✓  Kind cluster '{cluster_name}' — {len(node_lines)} node(s)")
    for line in node_lines:
        print(f"     {line}")
    return True


# ---


def section_3_namespaces() -> list[str]:
    """Section 3 — Check required namespaces."""
    print("\n=== Section 3: Namespaces ===")

    rc, out = run_cmd(
        "kubectl", "get", "namespaces",
        "--no-headers",
        "-o", "custom-columns=NAME:.metadata.name",
    )
    existing = set(out.splitlines()) if rc == 0 else set()

    missing: list[str] = []
    for ns in REQUIRED_NAMESPACES:
        if ns in existing:
            print(f"  ✓  namespace/{ns}")
        else:
            print(f"  ✗  namespace/{ns}  (missing)")
            missing.append(ns)

    if missing:
        cmds = "  &&  ".join(f"kubectl create namespace {ns}" for ns in missing)
        print(f"\n  Create missing: {cmds}")

    return missing


# ---


def section_4_deployments() -> list[str]:
    """Section 4 — Check expected deployments."""
    print("\n=== Section 4: Deployments ===")

    missing: list[str] = []
    for ns, name in REQUIRED_DEPLOYMENTS:
        rc, out = run_cmd(
            "kubectl", "get", "deployment", name,
            "-n", ns,
            "--no-headers",
        )
        if rc == 0 and out and not out.lower().startswith("no "):
            parts = out.split()
            ready = parts[2] if len(parts) > 2 else "?"
            print(f"  ✓  deployment/{name}  (namespace={ns}, ready={ready})")
        else:
            print(f"  ✗  deployment/{name}  (namespace={ns}) — not found")
            missing.append(f"{ns}/{name}")

    return missing


# ---


def section_5_apply_command() -> None:
    """Section 5 — Show the command to deploy ravi-engine."""
    print("\n=== Section 5: Deploy command ===")

    manifests_exist = MANIFESTS_DIR.exists()
    print(f"  Manifests dir : {MANIFESTS_DIR}")
    print(f"  Manifests found: {manifests_exist}")
    print()

    if manifests_exist:
        print("  To deploy ravi-engine to the Kind cluster:")
        print()
        print("    # Apply base manifests")
        print(f"    kubectl apply -k {MANIFESTS_DIR}/overlays/local")
        print()
        print("    # Watch rollout")
        print("    kubectl rollout status deployment/agent-backend -n agent-framework")
        print()
        print("    # Port-forward to test locally")
        print("    kubectl port-forward -n agent-framework svc/agent-backend 8000:8000")
    else:
        print("  Manifests directory not found. Expected layout:")
        print("    deployment/k8s/base/          — base Kustomize config")
        print("    deployment/k8s/overlays/local/ — local/dev overlay")


# ---


async def main() -> None:
    kubectl_ok = section_1_kubectl()
    if not kubectl_ok:
        print("\nFix kubectl first, then re-run.")
        sys.exit(1)

    context_ok = section_2_kind_context()
    if not context_ok:
        print("\nFix the cluster context first, then re-run.")
        sys.exit(1)

    missing_ns = section_3_namespaces()
    missing_deploys = section_4_deployments()
    section_5_apply_command()

    print("\n--- Summary ---")
    if not missing_ns and not missing_deploys:
        print("  Cluster is ready for ravi-engine.")
    else:
        if missing_ns:
            print(f"  Missing namespaces: {missing_ns}")
        if missing_deploys:
            print(f"  Missing deployments: {missing_deploys}")
        print("  Run the deploy command in Section 5 to bring everything up.")


if __name__ == "__main__":
    asyncio.run(main())
