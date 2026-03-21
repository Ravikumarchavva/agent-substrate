# Microservices Kubernetes Manifests

Complete Kubernetes deployment for the Agent Framework microservice architecture.

## Architecture

| Namespace | Services | Purpose |
|---|---|---|
| `af-edge` | Gateway BFF (8001) | Public API surface, SSE proxy, request routing |
| `af-platform` | Identity Auth (8010), Policy (8011), Admin (8019) | Auth, authorization, tenant admin |
| `af-runtime` | Conversation (8012), Workflow (8013), Agent Runtime (8014), Tool Executor (8015), Code Interpreter (8080), HITL (8016), Stream (8017), Artifact (8018) | Core agent execution pipeline |
| `af-data` | PostgreSQL, Redis | Shared data stores |
| `af-observability` | Tempo, Grafana | Tracing and dashboards |

## Manifests

| File | Contents |
|---|---|
| `00-namespaces.yaml` | 5 namespace definitions with layer labels |
| `01-secrets.yaml` | Secret templates (edge, platform, runtime) — **replace before deploy** |
| `10-gateway-bff.yaml` | Deployment + Service + SA + HPA |
| `11-identity-auth.yaml` | Deployment + Service + SA + HPA |
| `12-policy-authorization.yaml` | Deployment + Service + SA + HPA |
| `20-conversation.yaml` | Deployment + Service + SA + HPA |
| `21-workflow.yaml` | Deployment + Service + SA + HPA |
| `30-agent-runtime.yaml` | Deployment + Service + SA + HPA |
| `31-tool-executor.yaml` | Deployment + Service + SA + HPA |
| `32-code-interpreter.yaml` | **StatefulSet** + headless Service + regular Service + ConfigMap + HPA |
| `40-hitl.yaml` | Deployment + Service + SA + HPA |
| `41-stream.yaml` | Deployment + Service + SA + HPA |
| `42-artifact.yaml` | Deployment + Service + SA + PVC + HPA |
| `50-admin.yaml` | Deployment + Service + SA |
| `90-network-policies.yaml` | Deny-all defaults, selective ingress/egress per service |

## Apply

```bash
# Apply all resources
kubectl apply -k k8s/microservices

# Watch rollout
kubectl get pods -A -l app.kubernetes.io/part-of=agent-framework
```

## Security Notes

1. **Secrets:** The `01-secrets.yaml` contains template values. In production, use
   [sealed-secrets](https://github.com/bitnami-labs/sealed-secrets),
   [external-secrets-operator](https://external-secrets.io/), or HashiCorp Vault.
2. **Network policies:** Default-deny in every namespace. Only explicit service-to-service
   paths are permitted. Agent Runtime has HTTPS egress for OpenAI API calls.
3. **Service accounts:** Each service gets its own SA for RBAC binding.

## Scaling

Each service (except Admin) has an HPA:

| Service | Min | Max | Target CPU |
|---|---|---|---|
| Gateway BFF | 2 | 10 | 70% |
| Identity Auth | 2 | 6 | 70% |
| Policy | 2 | 6 | 70% |
| Conversation | 2 | 10 | 70% |
| Workflow | 2 | 8 | 70% |
| Agent Runtime | 2 | 20 | 60% |
| Tool Executor | 2 | 15 | 60% |
| Code Interpreter | 2 | 10 | 60% | **StatefulSet** — each pod = isolated VM pool |
| HITL | 2 | 6 | 70% |
| Stream | 2 | 15 | 60% |
| Artifact | 2 | 6 | 70% |
| Admin | 1 | — | — |
