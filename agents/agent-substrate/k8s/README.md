# Kubernetes Manifests

Kubernetes deployment for the Agent Framework microservices architecture.

## Directory Structure

```
k8s/
├── base/                          ← Production base manifests (kustomize base)
│   ├── kustomization.yaml         ← References all base resources
│   ├── namespaces.yaml            ← 5 namespaces (af-edge, af-platform, af-runtime, af-data, af-observability)
│   ├── secrets.yaml               ← Secret templates (replace values for production)
│   ├── ingress.yaml               ← Production ingress (TLS + cert-manager)
│   ├── edge/
│   │   └── gateway-bff.yaml       ← Deployment + Service + SA + HPA
│   ├── platform/
│   │   ├── identity-auth.yaml
│   │   └── policy-authorization.yaml
│   ├── runtime/
│   │   ├── agent-runtime.yaml
│   │   ├── conversation.yaml
│   │   ├── job-controller.yaml
│   │   ├── tool-executor.yaml
│   │   ├── code-interpreter.yaml  ← StatefulSet (Firecracker VMs, requires KVM)
│   │   ├── human-gate.yaml
│   │   ├── live-stream.yaml
│   │   ├── file-store.yaml
│   │   └── admin.yaml
│   └── policies/
│       ├── network-policies.yaml  ← Deny-all defaults + selective allow rules
│       └── pod-disruption-budgets.yaml
└── overlays/
    └── kind/                      ← Kind cluster overlay
        ├── kustomization.yaml     ← Inherits base, adds Kind-specific patches
        ├── infra.yaml             ← Postgres + Redis (af-data namespace)
        ├── frontend.yaml          ← Next.js frontend (af-edge namespace)
        ├── ingress.yaml           ← nginx ingress (no TLS, port 80)
        ├── observability.yaml     ← Loki + Promtail + Tempo + Prometheus + Grafana + Node Exporter
        └── smoke-test.ps1         ← Cluster health smoke tests
```

## Architecture

| Namespace | Services | Purpose |
|---|---|---|
| `af-edge` | Gateway BFF, Frontend | Public API surface, SSE proxy, UI |
| `af-platform` | Identity Auth, Policy | Authentication, authorization |
| `af-runtime` | Agent Runtime, Conversation, Job Controller, Tool Executor, Human Gate, Live Stream, File Store, Admin | Core agent execution pipeline |
| `af-data` | PostgreSQL, Redis | Shared data stores |
| `af-observability` | Loki, Promtail, Tempo, Prometheus, Grafana, Node Exporter | Logging, tracing, metrics, dashboards |

## Quick Start (Kind)

```bash
# Full deploy (builds images, loads into Kind, creates secrets, applies everything)
uv run python deploy.py

# Or apply manually:
kubectl apply -k k8s/overlays/kind/

# Smoke tests (Windows PowerShell)
.\k8s\overlays\kind\smoke-test.ps1
```

## Access (Kind)

| URL | Target |
|---|---|
| `http://localhost/` | Frontend (Next.js) |
| `http://localhost/chat` | Gateway BFF API |
| `http://localhost/grafana/` | Grafana dashboards |
| `http://localhost/health` | Gateway health check |

## Production Deploy

```bash
# Apply base manifests directly (after configuring secrets + ingress)
kubectl apply -k k8s/base/

# Or create a production overlay under k8s/overlays/prod/
```

## How Kustomize Works Here

The **base** contains canonical service definitions with production-ready settings
(multi-replica, resource limits, HPAs). The **Kind overlay** layers on top:

- Overrides container images to `agent-microservices-kind:local`
- Adds `uvicorn` commands (single image, per-service entrypoints)
- Sets `imagePullPolicy: IfNotPresent`
- Injects `OTLP_ENDPOINT` for tracing → Tempo
- Adds Kind-only resources (Postgres, Redis, frontend, observability)
- Replaces production ingress with Kind-specific (no TLS)
- Uses `emptyDir` for file-store (no PVC in Kind)

Secrets are managed outside kustomize — the deploy script creates them from `.env`.

## Security Notes

1. **Secrets:** `base/secrets.yaml` is a template. In production, use
   [sealed-secrets](https://github.com/bitnami-labs/sealed-secrets),
   [external-secrets-operator](https://external-secrets.io/), or Vault.
2. **Network policies:** Default-deny in every namespace. Only explicit
   service-to-service paths are permitted.
3. **Service accounts:** Each service gets its own SA for RBAC binding.

## Scaling

Each service (except Admin) has an HPA:

| Service | Min | Max | CPU Target |
|---|---|---|---|
| Gateway BFF | 2 | 10 | 70% |
| Identity Auth | 2 | 6 | 70% |
| Policy | 2 | 6 | 70% |
| Conversation | 2 | 10 | 70% |
| Job Controller | 2 | 8 | 70% |
| Agent Runtime | 2 | 20 | 60% |
| Tool Executor | 2 | 15 | 60% |
| Code Interpreter | 2 | 10 | 60% |
| Human Gate | 2 | 6 | 70% |
| Live Stream | 2 | 15 | 60% |
| File Store | 2 | 6 | 70% |
| Admin | 1 | — | — |

