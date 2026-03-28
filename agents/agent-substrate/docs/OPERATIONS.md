# Operations Guide — Agent Framework (Kind Cluster)

Quick reference for deploying updates, checking status, reading logs, and debugging the local Kind cluster.

---

## Prerequisites

| Tool | Purpose |
|---|---|
| `docker` | Build images |
| `kind` | Local k8s cluster |
| `kubectl` | Cluster management |
| `uv` | Python package manager |
| `pnpm` | Frontend package manager |

---

## Full Redeploy (from scratch)

Use this when you want to rebuild both images and re-apply everything from scratch.

```bash
# Works on Windows (PowerShell), Linux, macOS, and Git Bash
uv run python deploy.py

# Optional flags:
uv run python deploy.py --cluster-name dev --backend-tag agent-microservices-kind:local --frontend-tag chatbot-frontend-kind:local
```

The script automatically:
1. Reads secrets from `.env`
2. Builds backend Docker image (`agent-microservices-kind:local`)
3. Builds frontend Docker image (`chatbot-frontend-kind:local`) from `../ai-chatbot-ui/`
4. Loads both images into the Kind cluster `dev`
5. Deploys namespaces and infra (Postgres, Redis)
6. Creates secrets in all namespaces
7. Applies all k8s manifests via `kubectl apply -k k8s/overlays/kind`

---

## Partial Redeploy — Backend Only

When you only change Python code in `agent-framework/`:

```powershell
# 1. Rebuild backend image
docker build -t agent-microservices-kind:local .

# 2. Load into Kind cluster
kind load docker-image agent-microservices-kind:local --name dev

# 3. Restart all backend deployments
kubectl rollout restart deployment -n af-edge
kubectl rollout restart deployment -n af-platform
kubectl rollout restart deployment -n af-runtime

# 4. Watch rollout complete
kubectl rollout status deployment/gateway-bff -n af-edge --timeout=120s
```

---

## Partial Redeploy — Frontend Only

When you only change code in `ai-chatbot-ui/`:

```powershell
# From ai-chatbot-ui/ directory
cd ..\ai-chatbot-ui

# 1. Rebuild frontend image (NEXT_PUBLIC_API_URL="" → uses relative paths via ingress)
docker build --build-arg NEXT_PUBLIC_API_URL="" -t localhost/ai-chatbot-ui:latest .

# 2. Load into Kind cluster
kind load docker-image localhost/ai-chatbot-ui:latest --name dev

# 3. Update the frontend deployment to use the new image
kubectl set image deployment/frontend frontend=localhost/ai-chatbot-ui:latest -n af-edge

# 4. Restart to pick it up
kubectl rollout restart deployment/frontend -n af-edge

# 5. Watch it come up
kubectl rollout status deployment/frontend -n af-edge --timeout=120s
```

---

## Apply k8s Manifest Changes Only

When you edit YAML files in `k8s/` but don't need to rebuild images:

```powershell
kubectl apply -k k8s/overlays/kind
```

---

## Status & Health

### Quick overview — all pods
```powershell
kubectl get pods -A
```

### Per-namespace pods
```powershell
kubectl get pods -n af-edge        # frontend, gateway-bff
kubectl get pods -n af-platform    # identity-auth, policy-authorization
kubectl get pods -n af-runtime     # agent-runtime, conversation, job-controller, etc.
kubectl get pods -n af-data        # postgres, redis
kubectl get pods -n af-observability  # grafana, loki, tempo, prometheus
```

### Only show problem pods
```powershell
kubectl get pods -A --field-selector=status.phase!=Running | Where-Object { $_ -notmatch "Completed|code-interpreter" }
```

### Deployment health
```powershell
kubectl get deployments -A
```

### HPA status (autoscaler)
```powershell
kubectl get hpa -A
```

### Ingress rules
```powershell
kubectl describe ingress af-ingress -n af-edge
```

### Endpoint connectivity
```powershell
# Health check
curl http://localhost/health

# Should return threads list (empty array is fine)
curl http://localhost/threads

# Full smoke test
./k8s/overlays/kind/smoke-test.ps1
```

---

## Logs

### Frontend (Next.js)
```powershell
kubectl logs -n af-edge deployment/frontend --tail=100 -f
```

### Gateway BFF
```powershell
kubectl logs -n af-edge deployment/gateway-bff --tail=100 -f
```

### Agent Runtime (where the ReAct loop runs)
```powershell
kubectl logs -n af-runtime deployment/agent-runtime --tail=100 -f
```

### Job Controller
```powershell
kubectl logs -n af-runtime deployment/job-controller --tail=100 -f
```

### Identity / Auth
```powershell
kubectl logs -n af-platform deployment/identity-auth --tail=100 -f
```

### All logs from a namespace (last 50 lines per pod)
```powershell
kubectl logs -n af-runtime --selector="" --tail=50 --all-containers
```

### Follow logs from multiple pods matching a label
```powershell
kubectl logs -n af-runtime -l app=agent-runtime -f
```

### Previous crashed container logs
```powershell
kubectl logs -n af-edge deployment/frontend --previous
```

---

## Debugging

### Describe a failing pod
```powershell
kubectl describe pod <pod-name> -n <namespace>
# e.g.
kubectl describe pod -n af-edge -l app=frontend
```

### Exec into a running container
```powershell
kubectl exec -it -n af-edge deployment/gateway-bff -- /bin/sh
```

### Check events (shows scheduling failures, OOM kills, etc.)
```powershell
kubectl get events -n af-edge --sort-by='.lastTimestamp' | Select-Object -Last 20
kubectl get events -A --sort-by='.lastTimestamp' | Select-Object -Last 30
```

### Memory pressure — kill stuck pods
```powershell
# Delete all Pending pods (they'll reschedule if something frees up)
kubectl get pods -A --field-selector=status.phase=Pending -o json |
  kubectl delete -f -
```

### Force delete a stuck pod
```powershell
kubectl delete pod <pod-name> -n <namespace> --grace-period=0 --force
```

---

## Secrets

### Recreate secrets (after `.env` change)
```bash
# Re-run deploy script — it uses --dry-run=client | apply so it's idempotent
uv run python deploy.py
```

### View current secret keys (not values)
```powershell
kubectl get secret shared-secrets -n af-edge -o jsonpath='{.data}' | ConvertFrom-Json | Get-Member -MemberType NoteProperty | Select-Object Name
```

---

## Scaling

### Scale a deployment to 1 replica (memory-constrained single-node Kind)
```powershell
kubectl scale deployment <name> -n <namespace> --replicas=1
# e.g.
kubectl scale deployment gateway-bff -n af-edge --replicas=1
```

### Scale an HPA minimum
```powershell
$patch = '{"spec":{"minReplicas":1}}'
Set-Content "$env:TEMP\hpa.json" $patch
kubectl patch hpa <name> -n <namespace> --type=merge --patch-file "$env:TEMP\hpa.json"
```

---

## Observability

### Open Grafana dashboards
```
http://localhost/grafana/
```
Login: `admin` / `admin` (anonymous read also enabled)

Pre-built dashboards:
- **Service RED Metrics** — requests, errors, duration per service
- **Infrastructure** — CPU, memory, pod restarts
- **Log Analytics** — error rates, log search
- **Distributed Tracing** — trace explorer (Tempo)
- **Alerts Overview** — firing alerts

### Query logs directly (Loki)
In Grafana → Explore → Loki:
```logql
{namespace=~"af-.*"}                           # all agent-framework logs
{namespace="af-edge", app="gateway-bff"}       # gateway logs only
{namespace=~"af-.*"} |~ "(?i)error|exception" # errors across all services
```

### Query traces (Tempo)
In Grafana → Explore → Tempo → Search

---

## Local Dev (no cluster)

### Start backend (monolith mode)
```powershell
cd agent-framework
docker compose up -d postgres redis
uv run uvicorn agent_framework.server.app:app --port 8000 --reload
```

### Start frontend
```powershell
cd ai-chatbot-ui
pnpm dev     # runs on http://localhost:3000
```

### Run tests
```powershell
cd agent-framework
uv run pytest
```

### Lint & format
```powershell
uv run ruff check .
uv run ruff format .
```

---

## Port Reference

| Service | Local Dev Port | k8s (Kind) |
|---|---|---|
| Frontend (Next.js) | 3000 | `http://localhost/` |
| Backend API | 8000 | `http://localhost/chat`, `/threads`, etc. |
| PostgreSQL | 5432 | internal cluster only |
| Redis | 6379 | internal cluster only |
| Grafana | — | `http://localhost/grafana/` |
| MCP demo server | 9000 | docker compose `--profile mcp` |
