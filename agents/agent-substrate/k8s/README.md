# Kubernetes Deployment Guide

This directory contains production-ready Kubernetes manifests for the AI Agent Framework.
Every resource is explained below — what it is, why it exists, and how it connects to everything else.

---

## Table of Contents

1. [Full Architecture Diagram](#1-full-architecture-diagram)
2. [Namespace Layout](#2-namespace-layout)
3. [File Reference](#3-file-reference)
4. [ConfigMaps — non-secret config](#4-configmaps)
5. [Secrets — sensitive credentials](#5-secrets)
6. [RBAC — ServiceAccounts, Roles, RoleBindings](#6-rbac)
7. [StatefulSets — stateful workloads](#7-statefulsets)
8. [Deployments — stateless workloads](#8-deployments)
9. [Services — pod networking](#9-services)
10. [Ingress — external traffic](#10-ingress)
11. [HorizontalPodAutoscalers — auto-scaling](#11-horizontalpodautoscalers)
12. [PersistentVolumeClaims — storage](#12-persistentvolumeclaims)
13. [Code Interpreter Session Routing (deep dive)](#13-code-interpreter-session-routing-deep-dive)
14. [Apply Order — deploy step by step](#14-apply-order)
15. [Verify Deployment](#15-verify-deployment)
16. [TLS / SSL Setup](#16-tls--ssl-setup)
17. [Monitoring Stack](#17-monitoring-stack)
18. [Database Backup](#18-database-backup)
19. [Scaling Reference](#19-scaling-reference)
20. [Troubleshooting](#20-troubleshooting)
21. [Security Checklist](#21-security-checklist)
22. [Cost Reference](#22-cost-reference)

---

## 1. Full Architecture Diagram

```
Internet
    |  HTTPS :443 / :80
    v
+------------------------------------------------------------------+
|  namespace: ingress-nginx                                        |
|                                                                  |
|  +-------------+    +-----------------------+                   |
|  | cert-manager|    |  nginx Ingress        |<--LoadBalancer svc|
|  | (Let's      |--->|  Controller           |   :80 / :443      |
|  |  Encrypt)   |    |  (2 replicas)         |                   |
|  +-------------+    +----------+------------+                   |
|                                | routes by path                  |
+--------------------------------+----------------------------------+
                                 |
              +------------------+-----------------+
              |  /                                 |  /api/chat
              |  /api/auth                         |  /api/mcp
              |  /api/spotify                      |  /ui
              v                                    v
+-------------------------------------------------------------------------------------+
|  namespace: agent-framework                                                         |
|                                                                                     |
|  +------------------------------+   +----------------------------------------+    |
|  |  Deployment: agent-frontend  |   |  Deployment: agent-backend             |    |
|  |  SA: agent-frontend          |   |  SA: agent-backend --> k8s API         |    |
|  |  2-10 replicas (HPA)         |   |  3-20 replicas (HPA)                   |    |
|  |  image: agent-frontend:vX    |   |  image: agent-backend:vX               |    |
|  |                              |   |                                        |    |
|  |  Reads from:                 |   |  Reads from:                           |    |
|  |  +- agent-secrets            |   |  +- agent-secrets                      |    |
|  |  +- agent-config             |   |  +- agent-config                       |    |
|  +---------------+--------------+   +--------+-------------------------------+    |
|                  |                           |                                     |
|             ClusterIP svc              ClusterIP svc                               |
|             :3000                      :8001                                       |
|                                            |                                      |
|                    +---------------------+-+-------------------+                  |
|                    |                     |                     |                  |
|                    v                     v                     v                  |
|          +------------------+   +------------------+  +------------------+       |
|          |  StatefulSet:    |   |  StatefulSet:    |  |  StatefulSet:    |       |
|          |  postgres        |   |  code-interp.    |  |  redis           |       |
|          |  pgvector:pg16   |   |  0..N replicas   |  |  1 replica       |       |
|          |  1 replica       |   |  2-10 (HPA)      |  |                  |       |
|          |                  |   |  KVM/Firecracker  |  |  SSE event bus   |       |
|          |  Extensions:     |   |  privileged=true  |  |  HITL approvals  |       |
|          |  +- vector       |   |                  |  |  pub/sub broker  |       |
|          |  +- pg_trgm      |   |  2 Services:     |  |                  |       |
|          |  +- uuid-ossp    |   |  headless (DNS)  |  |  Port: 6379      |       |
|          |                  |   |  ClusterIP       |  |                  |       |
|          |  Port: 5432      |   |  + sessionAff.   |  |                  |       |
|          +--------+---------+   +--------+---------+  +------------------+       |
|                   |                      |                                        |
|          volumeClaimTemplate     headless svc + ClusterIP                        |
|          20Gi ReadWriteOnce      sessionAffinity: ClientIP 1800s                 |
|                                                                                   |
|  ConfigMaps:  agent-config | code-interpreter-config | postgres-init-scripts     |
|  Secrets:     agent-secrets                                                       |
|  RBAC:        3 ServiceAccounts | 1 Role | 1 RoleBinding                         |
|  HPA:         backend (3-20) | frontend (2-10) | code-interpreter (2-10)         |
|  PVCs:        postgres-pvc (20Gi) | postgres-backup-pvc (50Gi)                   |
+-------------------------------------------------------------------------------------+

+--------------------------------------------------------------+
|  namespace: monitoring                                        |
|                                                              |
|  Prometheus :9090  -->  Grafana :3000                        |
|  Loki :3100        -->  (dashboards)                         |
|  Tempo :4317  <--- OTEL traces from agent-backend            |
|                                                              |
|  PVCs: tempo-pvc | loki-pvc | prometheus-pvc | grafana-pvc  |
+--------------------------------------------------------------+
```

---

## 2. Namespace Layout

```
cluster
+-- ingress-nginx          <- nginx controller + cert-manager live here
+-- agent-framework        <- ALL application workloads
|   +-- frontend (Deployment)
|   +-- backend (Deployment)
|   +-- postgres / pgvector (StatefulSet)
|   +-- code-interpreter (StatefulSet)
|   +-- redis (StatefulSet - add redis-statefulset.yaml)
+-- monitoring             <- Prometheus, Grafana, Loki, Tempo
```

**Why separate namespaces?**
- Blast radius isolation: a misbehaving agent pod cannot directly reach monitoring infra
- RBAC is scoped per namespace — easier to grant least-privilege
- Resource quotas can be applied per namespace independently

---

## 3. File Reference

| File | Kind(s) | Purpose |
|---|---|---|
| `namespace.yaml` | Namespace | Creates `agent-framework` namespace |
| `configmap.yaml` | ConfigMap: `agent-config` | Non-secret app config (URLs, DB name, CI service addresses) |
| `code-interpreter-configmap.yaml` | ConfigMap: `code-interpreter-config` | Firecracker VM pool tuning (vCPU, RAM, pool size, timeouts) |
| `pgvector-init-configmap.yaml` | ConfigMap: `postgres-init-scripts` | SQL run once on fresh DB — installs `vector`, `pg_trgm`, `uuid-ossp` |
| `secrets.yaml` | Secret: `agent-secrets` | API keys, DB password, OAuth secrets — replace `CHANGEME_` before apply |
| `rbac.yaml` | ServiceAccount (x3), Role, RoleBinding | Least-privilege identities for each workload |
| `postgres-statefulset.yaml` | StatefulSet, volumeClaimTemplate | pgvector:pg16 database with init scripts |
| `postgres-service.yaml` | Service (headless) | `postgres:5432` ClusterIP for backend connection |
| `code-interpreter-statefulset.yaml` | StatefulSet, Service (headless), Service (ClusterIP) | Firecracker VM pool with graceful drain |
| `backend-deployment.yaml` | Deployment, Service | FastAPI agent backend |
| `frontend-deployment.yaml` | Deployment, Service | Next.js frontend |
| `ingress.yaml` | Ingress | nginx path routing + TLS via cert-manager |
| `hpa.yaml` | HPA x3 | Auto-scaling for backend, frontend, code-interpreter |
| `pvc.yaml` | PVC x2 | `postgres-pvc` (20Gi) + `postgres-backup-pvc` (50Gi) |

---

## 4. ConfigMaps

ConfigMaps hold **non-sensitive** configuration. Pods read them as environment variables.
Never put passwords or API keys here — use Secrets for those.

### `agent-config` (configmap.yaml)

```
Key                     Used by         Purpose
----------------------- --------------- ------------------------------------------
POSTGRES_DB             postgres pod    Database name: agent_framework
POSTGRES_USER           postgres pod    DB user: agent
NEXTAUTH_URL            frontend        Full public URL for NextAuth callbacks
NEXT_PUBLIC_API_URL     frontend        URL frontend uses to call backend
SPOTIFY_REDIRECT_URI    frontend        OAuth callback URL
GOOGLE_REDIRECT_URI     frontend        OAuth callback URL
NODE_ENV                frontend        "production"
PORT                    backend         FastAPI listen port: 8000
CODE_INTERPRETER_URL    backend         Load-balanced CI entry (fallback only)
CI_REPLICAS             backend         Fallback replica count (live k8s API preferred)
CI_HEADLESS_SERVICE     backend         "code-interpreter-headless"
CI_NAMESPACE            backend         "agent-framework"
```

### `code-interpreter-config` (code-interpreter-configmap.yaml)

```
Key                 Default   Purpose
------------------- --------- -----------------------------------------------
CI_FC_VCPU_COUNT    1         vCPUs per Firecracker microVM
CI_FC_MEM_SIZE_MIB  256       RAM per microVM (MiB)
CI_POOL_SIZE        3         Pre-warmed VMs kept ready per pod
CI_POOL_MAX_SIZE    16        Max simultaneous VMs per pod
CI_SESSION_TIMEOUT  1800      Seconds before idle session is destroyed (30 min)
CI_MAX_SESSIONS     50        Hard cap on concurrent sessions per pod
CI_DEFAULT_TIMEOUT  30        Default code execution timeout (seconds)
CI_MAX_TIMEOUT      300       Maximum allowed execution timeout (seconds)
CI_WORK_DIR         /tmp/...  Socket files and overlay rootfs copies
```

### `postgres-init-scripts` (pgvector-init-configmap.yaml)

```
Script                  Runs when           Purpose
----------------------- ------------------- -----------------------------------------
01-pgvector.sql         First DB startup    CREATE EXTENSION vector, pg_trgm, uuid-ossp
02-vector-tables.sql    First DB startup    Placeholder for vector DDL (real DDL via Alembic)
```

> **Important**: Init scripts only run on a **fresh (empty) database**.
> For an existing database, run manually:
>
> ```bash
> kubectl exec -it postgres-0 -n agent-framework -- \
>   psql -U agent -d agent_framework -c "CREATE EXTENSION IF NOT EXISTS vector;"
> ```

---

## 5. Secrets

`secrets.yaml` defines the `agent-secrets` Secret. **Never commit real values to git.**
Replace every `CHANGEME_` value before applying, or create the Secret directly:

```bash
kubectl create secret generic agent-secrets \
  --from-literal=DB_PASSWORD='your-db-password' \
  --from-literal=DATABASE_URL='postgresql+asyncpg://agent:your-db-password@postgres:5432/agent_framework' \
  --from-literal=ENCRYPTION_KEY="$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')" \
  --from-literal=NEXTAUTH_SECRET="$(openssl rand -hex 32)" \
  --from-literal=OPENAI_API_KEY='sk-...' \
  --from-literal=ANTHROPIC_API_KEY='sk-ant-...' \
  --from-literal=SPOTIFY_CLIENT_ID='...' \
  --from-literal=SPOTIFY_CLIENT_SECRET='...' \
  --from-literal=GOOGLE_CLIENT_ID='....apps.googleusercontent.com' \
  --from-literal=GOOGLE_CLIENT_SECRET='...' \
  -n agent-framework
```

```
Secret key          Consumed by               Notes
------------------- ------------------------- ---------------------------------
DB_PASSWORD         postgres pod              Postgres user password
DATABASE_URL        backend + frontend        Full asyncpg connection string
ENCRYPTION_KEY      backend                   Fernet key for stored credentials
NEXTAUTH_SECRET     frontend                  Signs session JWTs
OPENAI_API_KEY      backend                   LLM API key
ANTHROPIC_API_KEY   backend                   Optional second LLM provider
SPOTIFY_CLIENT_*    backend + frontend        OAuth app credentials
GOOGLE_CLIENT_*     backend + frontend        OAuth app credentials
CI_AUTH_TOKEN       code-interpreter pods     Bearer token for CI API (optional)
```

---

## 6. RBAC

`rbac.yaml` creates three ServiceAccounts and a Role scoped to the `agent-framework` namespace.

```
ServiceAccount          Bound to               Permissions granted
----------------------- ---------------------- -----------------------------------------------
agent-backend           agent-backend pods     GET/LIST/WATCH pods in namespace
                                               GET statefulsets/code-interpreter
                                               (needed for live CI pod discovery)
agent-frontend          agent-frontend pods    None — no k8s API access needed
agent-code-interpreter  code-interpreter pods  None — isolated execution environment
```

### Why does the backend need pod-list access?

The code interpreter uses **consistent-hash session routing** (see Section 13).
The backend must know the real-time count of *ready* CI pods to hash thread IDs correctly.
This requires querying the k8s API instead of relying on the stale `CI_REPLICAS` ConfigMap value:

```python
# Add to: src/agent_framework/tools/code_interpreter_tool.py
# Add dependency: uv add kubernetes

from kubernetes import client, config
import hashlib

config.load_incluster_config()  # token auto-mounted at /var/run/secrets/kubernetes.io/serviceaccount/
v1 = client.CoreV1Api()
pods = v1.list_namespaced_pod(
    namespace="agent-framework",
    label_selector="app=code-interpreter"
)
ready = [p for p in pods.items if p.status.phase == "Running"]
idx   = int(hashlib.sha1(thread_id.encode()).hexdigest(), 16) % len(ready)
url   = (
    f"http://{ready[idx].metadata.name}"
    f".code-interpreter-headless.agent-framework.svc.cluster.local:8080"
)
```

```
Role: backend-pod-reader
  rules:
    +-- apiGroups: [""]    resources: [pods]         verbs: [get, list, watch]
    +-- apiGroups: [apps]  resources: [statefulsets]  verbs: [get]
                           resourceNames: [code-interpreter]  <- scoped to ONE StatefulSet
```

---

## 7. StatefulSets

StatefulSets give pods **stable, ordered identities** (`pod-0`, `pod-1`, ...) and **stable DNS names**.
Used for anything that holds state that must survive pod restarts.

---

### 7.1 postgres StatefulSet

```
postgres-statefulset.yaml

StatefulSet: postgres
  replicas:   1
  image:      pgvector/pgvector:pg16     <- postgres 16 + pgvector extension pre-installed
  port:       5432
  serviceName: postgres                  <- links to headless service (postgres-service.yaml)

  Environment (from ConfigMap: agent-config):
    POSTGRES_DB   = agent_framework
    POSTGRES_USER = agent

  Environment (from Secret: agent-secrets):
    POSTGRES_PASSWORD = DB_PASSWORD

  VolumeMounts:
    /var/lib/postgresql/data      <- postgres-storage (volumeClaimTemplate, 20Gi)
    /docker-entrypoint-initdb.d   <- postgres-init-scripts (ConfigMap, read-only)
                                     SQL here runs ONCE on first startup

  Probes:
    liveness:  pg_isready -U agent   every 10s
    readiness: pg_isready -U agent   every 5s

  volumeClaimTemplate: postgres-storage
    storageClass: local-path
    size:         20Gi
    accessMode:   ReadWriteOnce

  Volumes (non-persistent):
    init-scripts  <- mounts postgres-init-scripts ConfigMap

  Resources:
    requests: 512Mi RAM, 500m CPU
    limits:   2Gi RAM,   2000m CPU
```

**pgvector** means your single PostgreSQL instance handles both:
- Regular relational data (threads, messages, users, tasks)
- High-dimensional vector similarity search for agent memory and RAG

```
Agent stores embedding --> INSERT INTO memory(embedding) VALUES($1::vector)
Agent recalls context  --> SELECT * FROM memory
                           ORDER BY embedding <=> $query   -- <=> = cosine distance
                           LIMIT 5
```

---

### 7.2 code-interpreter StatefulSet

```
code-interpreter-statefulset.yaml

StatefulSet: code-interpreter
  replicas:            2  (scaled 2-10 by HPA)
  podManagementPolicy: Parallel     <- all pods start simultaneously
  serviceName:         code-interpreter-headless
  containerPort:       8080

  nodeSelector: firecracker=true    <- only runs on nodes with /dev/kvm

  ServiceAccount:              agent-code-interpreter
  terminationGracePeriodSeconds: 300  <- gives drain hook up to 5 min

  lifecycle.preStop hook:
    1. Calls  GET /v1/drain               <- server stops accepting new sessions
    2. Polls  GET /v1/sessions/active-count  every 5s
    3. Exits when count == 0  OR  240s elapsed
    (k8s sends SIGTERM after 300s, leaving 60s headroom)

  Volumes:
    /data             (hostPath /opt/code-interpreter, read-only) <- kernel + rootfs
    /dev/kvm          (hostPath CharDevice)                       <- KVM acceleration
    /dev/vhost-vsock  (hostPath CharDevice)                       <- host<->VM comms
    /tmp/firecracker-vms (emptyDir 20Gi)                          <- ephemeral workspace

  Resources:
    requests: 1Gi RAM, 500m CPU
    limits:   4Gi RAM, 4000m CPU

  Probes:
    startup:   GET /v1/health        failureThreshold: 12 (70s max startup)
    liveness:  GET /v1/health        every 15s
    readiness: GET /v1/health/ready  every 10s
```

**Two Services back the StatefulSet:**

```
Service: code-interpreter-headless  (clusterIP: None)
  Enables per-pod DNS:
    code-interpreter-0.code-interpreter-headless.agent-framework.svc.cluster.local:8080
    code-interpreter-1.code-interpreter-headless.agent-framework.svc.cluster.local:8080
    code-interpreter-N... (one entry per ready pod)

Service: code-interpreter  (ClusterIP + sessionAffinity: ClientIP, timeout: 1800s)
  Fallback load-balanced access
  sessionAffinity pins a backend pod's IP to one CI pod for 30 min
  Backend should prefer per-pod DNS (headless) for deterministic routing
```

---

## 8. Deployments

Deployments are **stateless** — any pod is identical and replaceable.

### 8.1 agent-backend

```
backend-deployment.yaml

Deployment: agent-backend
  replicas:       3  (scaled 3-20 by HPA)
  ServiceAccount: agent-backend
  containerPort:  8000

  Environment from ConfigMap:
    PORT, CODE_INTERPRETER_URL, CI_REPLICAS, CI_HEADLESS_SERVICE, CI_NAMESPACE

  Environment from Secret:
    DATABASE_URL, OPENAI_API_KEY, ANTHROPIC_API_KEY, ENCRYPTION_KEY,
    SPOTIFY_CLIENT_*, GOOGLE_CLIENT_*, CI_AUTH_TOKEN

  Probes:
    liveness:  GET /health  every 10s  (after 30s)
    readiness: GET /health  every 5s   (after 10s)

  Resources:
    requests: 1Gi RAM, 500m CPU
    limits:   4Gi RAM, 2000m CPU

Service: agent-backend
  type: ClusterIP
  port: 8001 -> containerPort 8000
```

### 8.2 agent-frontend

```
frontend-deployment.yaml

Deployment: agent-frontend
  replicas:       2  (scaled 2-10 by HPA)
  ServiceAccount: agent-frontend
  containerPort:  3000

  Environment from ConfigMap:
    NEXTAUTH_URL, NEXT_PUBLIC_API_URL,
    SPOTIFY_REDIRECT_URI, GOOGLE_REDIRECT_URI, NODE_ENV

  Environment from Secret:
    DATABASE_URL, ENCRYPTION_KEY, NEXTAUTH_SECRET,
    SPOTIFY_CLIENT_*, GOOGLE_CLIENT_*

  Probes:
    liveness:  GET /  every 10s  (after 30s)
    readiness: GET /  every 5s   (after 10s)

  Resources:
    requests: 512Mi RAM, 250m CPU
    limits:   2Gi RAM, 1000m CPU

Service: agent-frontend
  type: ClusterIP
  port: 3000
```

---

## 9. Services

A Service gives a stable IP and DNS name to a set of pods. Pods come and go; the Service IP never changes.

```
Service name                  Type        Port    Selects                    Used by
----------------------------  ----------  ------  -------------------------  -----------------------
postgres                      headless    5432    app=postgres               backend, frontend
agent-backend                 ClusterIP   8001    app=agent-backend          ingress, frontend
agent-frontend                ClusterIP   3000    app=agent-frontend         ingress
code-interpreter              ClusterIP   8080    app=code-interpreter       backend (fallback)
code-interpreter-headless     headless    8080    app=code-interpreter       backend (per-pod DNS)
LoadBalancer (nginx ctrl)     LB          80/443  nginx pods                 internet
```

**Headless service** (`clusterIP: None`) — k8s returns the individual pod IPs in DNS
instead of a single virtual IP. This is what makes stable per-pod addressing work:
`pod-0.headless-svc.namespace.svc.cluster.local`

---

## 10. Ingress

`ingress.yaml` routes external HTTPS traffic to the right service based on URL path.

```
HTTPS request arrives at LoadBalancer :443
    |
    v  nginx Ingress Controller decrypts TLS (cert from cert-manager / Let's Encrypt)
    |
    +-- /api/auth/*       -->  agent-frontend:3000  (NextAuth OAuth endpoints)
    +-- /api/spotify/*    -->  agent-frontend:3000  (Spotify OAuth)
    +-- /api/google/*     -->  agent-frontend:3000  (Google OAuth)
    +-- /api/chat/*       -->  agent-backend:8001   (SSE streaming chat)
    +-- /api/mcp/*        -->  agent-backend:8001   (MCP tool API)
    +-- /ui/*             -->  agent-backend:8001   (FastAPI UI endpoints)
    +-- / (catch-all)     -->  agent-frontend:3000  (Next.js app)

Annotations applied:
  ssl-redirect: true              <- HTTP -> HTTPS redirect
  proxy-read-timeout: 300s        <- allows long SSE streams (agent reasoning loops)
  proxy-send-timeout: 300s
  limit-rps: 10                   <- rate limiting per IP
  limit-burst-multiplier: 2
  Strict-Transport-Security       <- HSTS header
  X-Frame-Options: SAMEORIGIN
  X-Content-Type-Options: nosniff
```

> **SSE note**: `proxy-read-timeout: 300s` is critical.
> Without it nginx closes connections after 60s and streaming chat breaks mid-response.

---

## 11. HorizontalPodAutoscalers

HPA watches CPU/memory of pods and adjusts the replica count automatically.

### agent-backend-hpa

```
Target:  Deployment/agent-backend
Min: 3   Max: 20

Scale UP   trigger: CPU > 70%  OR  Memory > 80%
  stabilization: 0s      (scale up immediately - agent bursts are sudden)
  policy: max(double replicas, add 4 pods) per 15s

Scale DOWN trigger: CPU < 70%  AND  Memory < 80%
  stabilization: 300s    (wait 5 min - agent calls can be slow to finish)
  policy: remove max 50% per 60s
```

### agent-frontend-hpa

```
Target:  Deployment/agent-frontend
Min: 2   Max: 10

Scale UP   trigger: CPU > 70%  OR  Memory > 80%
  stabilization: 0s
  policy: max(double replicas, add 2 pods) per 15s

Scale DOWN trigger: stabilization: 300s   remove max 50% per 60s
```

### code-interpreter-hpa

```
Target:  StatefulSet/code-interpreter
Min: 2   Max: 10

Scale UP   trigger: CPU > 60%  OR  Memory > 70%
  stabilization: 30s
  policy: add 1 pod per 60s      <- conservative; each pod is expensive (KVM VMs)

Scale DOWN trigger: stabilization: 600s   remove 1 pod per 120s
  (long window because active VM sessions may look idle between code calls)
  preStop hook drains sessions before pod is killed (see Section 13)
```

---

## 12. PersistentVolumeClaims

A PVC is a request for physical storage. k8s binds it to a PersistentVolume on the node.

```
Name                       Size   AccessMode      StorageClass  Owner
-------------------------- -----  --------------  ------------  --------------------------------
postgres-pvc               20Gi   ReadWriteOnce   local-path    pvc.yaml (standalone, for tools)
postgres-backup-pvc        50Gi   ReadWriteOnce   local-path    pvc.yaml (for CronJob backups)
postgres-storage-postgres-0 20Gi  ReadWriteOnce   local-path    volumeClaimTemplate in postgres STS
                                                                auto-created, follows pod on restart
```

> **Two postgres PVCs?**
> `postgres-pvc` (pvc.yaml) is a standalone claim kept for backup tooling and manual access.
> The actual live database data lives in `postgres-storage-postgres-0`, which is auto-created by the
> StatefulSet's `volumeClaimTemplates` — it follows the pod across restarts and node moves.

**On cloud (production)** replace `storageClassName: local-path` with:

```
AWS EKS  ->  gp3
GKE      ->  premium-rwo
AKS      ->  managed-premium
```

---

## 13. Code Interpreter Session Routing (deep dive)

This is the most complex part of the system. Here is how it works end-to-end.

### The problem

A ReAct agent may call the code interpreter 5+ times in one conversation.
Each call must hit the **same pod** because the Firecracker microVM (with its installed packages
and in-memory variables) lives inside that one pod's RAM.

### How StatefulSet + consistent hashing solves it

```
Step 1 - StatefulSet gives every pod a stable, predictable DNS name:

  code-interpreter-0.code-interpreter-headless.agent-framework.svc.cluster.local
  code-interpreter-1.code-interpreter-headless.agent-framework.svc.cluster.local
  code-interpreter-N ...
  (DNS only resolves when the pod is Ready)


Step 2 - Backend picks pod index by hashing the thread_id:

  thread_id  = "thread_abc123"
  n_ready    = 3  (queried LIVE from k8s API - not stale ConfigMap)
  idx        = SHA1("thread_abc123") % 3  =  1   <- deterministic, same every call
  url        = http://code-interpreter-1.code-interpreter-headless...


Step 3 - k8s API gives the live ready pod count (RBAC in rbac.yaml grants this):

  from kubernetes import client, config
  import hashlib

  config.load_incluster_config()       # reads token from /var/run/secrets/...
  v1 = client.CoreV1Api()
  pods = v1.list_namespaced_pod(
      namespace="agent-framework",
      label_selector="app=code-interpreter"
  )
  ready = [p for p in pods.items if p.status.phase == "Running"]
  n     = len(ready)
  idx   = int(hashlib.sha1(thread_id.encode()).hexdigest(), 16) % n
  url   = (
      f"http://{ready[idx].metadata.name}"
      f".code-interpreter-headless.agent-framework.svc.cluster.local:8080"
  )
  # Add: uv add kubernetes
```

### What happens when HPA scales UP (2 -> 3 pods)

```
Before scale-up:
  pod-0           pod-1              hash % 2
  thread-t1, t3   thread-t2, t4

After scale-up (pod-2 added, n=3):
  pod-0    pod-1    pod-2 (NEW)      hash % 3
  t1       t2       ---

  t3, t4 get re-hashed on their NEXT tool call.
  They may land on a different pod -> a fresh VM starts for them.
  Impact: agent may need to re-import packages once. Acceptable.
```

### What happens when HPA scales DOWN (3 -> 2 pods)

```
k8s always terminates the HIGHEST-index pod first (StatefulSet guarantee):
  code-interpreter-2 is chosen for termination.

  preStop hook fires on pod-2:
    1.  POST /v1/drain              <- reject new sessions immediately
    2.  Poll /v1/sessions/active-count  every 5s
    3.  Wait until count==0  OR  240s elapsed
    4.  Exit cleanly (k8s SIGTERM fires at 300s - 60s headroom)

  Pods 0 and 1 are completely undisturbed.
  Threads that hashed to pod-2 get re-hashed to pod-0 or pod-1 on next call.
```

### Visual timeline of a multi-tool-call conversation

```
Thread "abc" ----------------------------------------------------------------->

  Tool call 1  -> hash("abc") % 2 = 1  -> code-interpreter-1  (VM starts)
  Tool call 2  -> hash("abc") % 2 = 1  -> code-interpreter-1  (SAME VM, state preserved)
  Tool call 3  -> hash("abc") % 2 = 1  -> code-interpreter-1  (SAME VM, state preserved)
                                          import pandas done in call 1 still works in call 3 OK

  [HPA adds pod-2 between call 3 and call 4; n_ready goes 2 -> 3]

  Tool call 4  -> hash("abc") % 3 = 2  -> code-interpreter-2  (NEW VM - state reset)
                                          agent must re-import pandas here
```

### sessionAffinity as a secondary safety net

The `code-interpreter` ClusterIP service has `sessionAffinity: ClientIP` with a 1800s timeout.
If the backend falls back to the load-balanced service URL (instead of per-pod DNS),
the same backend pod will always be routed to the same CI pod for up to 30 minutes.
This is not a replacement for the hash routing — it is a fallback.

---

## 14. Apply Order

Resources must be applied in dependency order:

```bash
# 0. Prerequisites: kubectl configured, images pushed to registry, CHANGEME_ values set

# 1. Namespace first (everything else needs it to exist)
kubectl apply -f namespace.yaml

# 2. ConfigMaps and Secrets (pods read these at startup)
kubectl apply -f configmap.yaml
kubectl apply -f code-interpreter-configmap.yaml
kubectl apply -f pgvector-init-configmap.yaml
kubectl apply -f secrets.yaml

# 3. RBAC (ServiceAccounts referenced by pod specs below)
kubectl apply -f rbac.yaml

# 4. Storage
kubectl apply -f pvc.yaml

# 5. Database
kubectl apply -f postgres-statefulset.yaml
kubectl apply -f postgres-service.yaml
kubectl wait --for=condition=ready pod -l app=postgres -n agent-framework --timeout=300s

# 6. Prisma migrations (Next.js schema)
kubectl run -it --rm migration \
  --image=your-registry/agent-frontend:latest \
  --restart=Never \
  --env="DATABASE_URL=postgresql://agent:${DB_PASSWORD}@postgres:5432/agent_framework" \
  -n agent-framework \
  -- pnpm prisma migrate deploy

# 7. Code interpreter
kubectl apply -f code-interpreter-statefulset.yaml
kubectl wait --for=condition=ready pod -l app=code-interpreter -n agent-framework --timeout=120s

# 8. Application workloads
kubectl apply -f backend-deployment.yaml
kubectl apply -f frontend-deployment.yaml

# 9. Ingress
kubectl apply -f ingress.yaml

# 10. Auto-scaling (last, after all pods are healthy)
kubectl apply -f hpa.yaml
```

Or use the provided scripts:
```bash
.\deploy.ps1    # Windows PowerShell
bash deploy.sh  # Linux / Mac
```

---

## 15. Verify Deployment

```bash
# All pods running?
kubectl get pods -n agent-framework -o wide

# Expected:
# agent-backend-XXXX        1/1  Running
# agent-backend-XXXX        1/1  Running
# agent-backend-XXXX        1/1  Running
# agent-frontend-XXXX       1/1  Running
# agent-frontend-XXXX       1/1  Running
# code-interpreter-0        1/1  Running
# code-interpreter-1        1/1  Running
# postgres-0                1/1  Running

# Services
kubectl get svc -n agent-framework

# HPA (shows current vs target replica count)
kubectl get hpa -n agent-framework

# Ingress (get external IP)
kubectl get ingress -n agent-framework

# pgvector installed?
kubectl exec -it postgres-0 -n agent-framework -- \
  psql -U agent -d agent_framework \
  -c "SELECT extname, extversion FROM pg_extension WHERE extname = 'vector';"

# CI headless DNS resolves per-pod?
kubectl exec -it deployment/agent-backend -n agent-framework -- \
  nslookup code-interpreter-0.code-interpreter-headless.agent-framework.svc.cluster.local

# Backend has pod-list RBAC?
kubectl auth can-i list pods \
  --as=system:serviceaccount:agent-framework:agent-backend \
  -n agent-framework
# Should print: yes

# Logs
kubectl logs -f deployment/agent-backend     -n agent-framework
kubectl logs -f deployment/agent-frontend    -n agent-framework
kubectl logs -f statefulset/code-interpreter -n agent-framework
kubectl logs -f statefulset/postgres         -n agent-framework
```

---

## 16. TLS / SSL Setup

### Install cert-manager

```bash
kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/v1.14.0/cert-manager.yaml
kubectl wait --for=condition=ready pod -l app=cert-manager -n cert-manager --timeout=120s
```

### Create ClusterIssuer (Let's Encrypt production)

```yaml
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: letsencrypt-prod
spec:
  acme:
    server: https://acme-v02.api.letsencrypt.org/directory
    email: your-email@example.com
    privateKeySecretRef:
      name: letsencrypt-prod
    solvers:
    - http01:
        ingress:
          class: nginx
```

Apply it, then annotate ingress:
```yaml
annotations:
  cert-manager.io/cluster-issuer: "letsencrypt-prod"
```

---

## 17. Monitoring Stack

```bash
# Install kube-prometheus-stack (Prometheus + Grafana + Alertmanager + node-exporter)
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update
helm install monitoring prometheus-community/kube-prometheus-stack \
  -n monitoring --create-namespace \
  --set grafana.adminPassword=changeme

# Access Grafana
kubectl port-forward svc/monitoring-grafana 3001:80 -n monitoring
# http://localhost:3001   admin / changeme
```

The backend emits **OpenTelemetry traces** to Tempo at `http://tempo:4317` in the `monitoring` namespace.
Add Tempo as a Grafana data source to trace individual ReAct reasoning loops across pod boundaries.

---

## 18. Database Backup

### Manual backup

```bash
kubectl exec -it postgres-0 -n agent-framework -- \
  pg_dump -U agent agent_framework | gzip > backup-$(date +%Y%m%d).sql.gz
```

### Restore

```bash
gunzip < backup-20260311.sql.gz | \
  kubectl exec -i postgres-0 -n agent-framework -- psql -U agent agent_framework
```

### Automated CronJob (daily at 2 AM)

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: postgres-backup
  namespace: agent-framework
spec:
  schedule: "0 2 * * *"
  jobTemplate:
    spec:
      template:
        spec:
          containers:
          - name: backup
            image: pgvector/pgvector:pg16
            command:
            - /bin/sh
            - -c
            - pg_dump -h postgres -U agent agent_framework | gzip > /backup/backup-$(date +%Y%m%d).sql.gz
            env:
            - name: PGPASSWORD
              valueFrom:
                secretKeyRef:
                  name: agent-secrets
                  key: DB_PASSWORD
            volumeMounts:
            - name: backup
              mountPath: /backup
          volumes:
          - name: backup
            persistentVolumeClaim:
              claimName: postgres-backup-pvc
          restartPolicy: OnFailure
```

---

## 19. Scaling Reference

### Manual scaling

```bash
kubectl scale deployment agent-backend    --replicas=10 -n agent-framework
kubectl scale deployment agent-frontend   --replicas=5  -n agent-framework
kubectl scale statefulset code-interpreter --replicas=4 -n agent-framework
```

### HPA limits summary

| Workload | Min | Max | CPU trigger | Scale-up speed | Scale-down wait |
|---|---|---|---|---|---|
| agent-backend | 3 | 20 | 70% | immediate, x2 or +4 pods / 15s | 5 min |
| agent-frontend | 2 | 10 | 70% | immediate, x2 or +2 pods / 15s | 5 min |
| code-interpreter | 2 | 10 | 60% | 30s wait, +1 pod / 60s | 10 min |

### Cost estimate (AWS t3 nodes)

| Environment | Config | Est. monthly |
|---|---|---|
| Dev | 1 backend, 1 frontend, 2 CI, 1 postgres; 2x t3.medium | $80-120 |
| Prod baseline | 3 backend, 2 frontend, 2 CI, 1 postgres; 3x t3.large | $350-500 |
| Prod high load | 10 backend, 5 frontend, 6 CI; 6x t3.xlarge | $900-1400 |

Reduce cost in dev by patching replica counts:
```bash
kubectl patch deployment agent-backend  -p '{"spec":{"replicas":1}}' -n agent-framework
kubectl patch deployment agent-frontend -p '{"spec":{"replicas":1}}' -n agent-framework
kubectl patch hpa agent-backend-hpa     -p '{"spec":{"minReplicas":1}}' -n agent-framework
kubectl patch hpa agent-frontend-hpa    -p '{"spec":{"minReplicas":1}}' -n agent-framework
```

---

## 20. Troubleshooting

### Pod stuck in Pending

```bash
kubectl describe pod <pod-name> -n agent-framework
# Look for:
#   "Insufficient memory/cpu"  -> node too small
#   "No nodes matching nodeSelector: firecracker=true"  -> label the node:
kubectl label node <node-name> firecracker=true
```

### Pod stuck in CrashLoopBackOff

```bash
kubectl logs <pod-name> -n agent-framework --previous   # last crash logs
kubectl describe pod <pod-name> -n agent-framework       # events section
```

### Database connection refused

```bash
kubectl get pods -l app=postgres -n agent-framework
kubectl exec -it postgres-0 -n agent-framework -- psql -U agent agent_framework
```

### pgvector extension missing (existing database)

```bash
kubectl exec -it postgres-0 -n agent-framework -- \
  psql -U agent -d agent_framework \
  -c "CREATE EXTENSION IF NOT EXISTS vector;
      CREATE EXTENSION IF NOT EXISTS pg_trgm;
      CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\";"
```

### SSE chat stream drops after 60s

```bash
kubectl get ingress agent-ingress -n agent-framework -o yaml | grep timeout
# proxy-read-timeout and proxy-send-timeout must be >= 300
```

### Code interpreter starts a new VM every tool call (state lost)

1. Check `thread_id` is consistent across all tool calls in a conversation
2. Verify backend ServiceAccount has pod-list RBAC:
   ```bash
   kubectl auth can-i list pods \
     --as=system:serviceaccount:agent-framework:agent-backend \
     -n agent-framework
   # must print: yes
   ```
3. Verify CI pods are Running (not just Scheduled):
   ```bash
   kubectl get pods -l app=code-interpreter -n agent-framework
   ```
4. Check `kubernetes` package is installed in backend:
   ```bash
   uv add kubernetes
   ```

### HPA not scaling

```bash
kubectl describe hpa -n agent-framework
# "unable to fetch metrics" -> metrics-server not installed:
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
```

---

## 21. Security Checklist

- [ ] All `CHANGEME_` values replaced in `secrets.yaml` (or Secret created via kubectl)
- [ ] `secrets.yaml` added to `.gitignore`
- [ ] HTTPS enforced (`ssl-redirect: true` in ingress annotations)
- [ ] RBAC: each workload uses its own ServiceAccount (`rbac.yaml` applied)
- [ ] NetworkPolicy: add `networkpolicy.yaml` to isolate code-interpreter from postgres/redis
- [ ] PodDisruptionBudget: add `pdb.yaml` with `minAvailable: 2` for backend
- [ ] `storageClassName: local-path` replaced with cloud provider SC before multi-node deploy
- [ ] Vulnerability scanning on images (Snyk / Trivy in CI)
- [ ] cert-manager + Let's Encrypt ClusterIssuer configured
- [ ] Rate limiting active (`limit-rps: 10` in ingress annotations)
- [ ] Postgres not exposed outside cluster (headless ClusterIP only)
- [ ] Automated database backups running (CronJob applied)
- [ ] Monitoring alerts configured in Grafana (pod restarts, error rates, HPA at max)
- [ ] `kubernetes` Python package added to backend: `uv add kubernetes`

---

## 22. Cost Reference

See Section 19 for per-environment breakdown.

For a quick teardown/rebuild dev cycle:
```bash
# Tear down all workloads but keep PVCs (database survives)
kubectl delete deployment,statefulset,hpa -n agent-framework --all

# Full reset (WARNING: destroys database data)
kubectl delete namespace agent-framework
```
