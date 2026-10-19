# Deploying a Loamz cell

A **cell** ([ADR 0006](../docs/adr/0006-cells-and-control-plane-split.md)) is one complete, identical deployment: Keycloak, the agent
server (monolith or microservices, [ADR 0009](../docs/adr/0009-microservices-mode-is-kept.md)), loamz-data (API, controller, workers),
the two web UIs, PostgreSQL, Redis and S3-compatible storage. The four hosting models run the same cell; they differ in who operates
it and how many tenants (Keycloak organizations) it serves.

| Directory | What |
| --- | --- |
| [`docker/`](docker) | One Dockerfile per image, all built from the **repository root** (`docker build -f deploy/docker/X.Dockerfile .`), using the single `uv.lock` / `pnpm-lock.yaml`. Non-root, health-checked. |
| [`compose/`](compose) | Self-host bundle: `compose.yml`, `.env.example`, `gen-env.sh`, bootstrap (`init/`, `initdb/`), an optional `Caddyfile`. |
| [`helm/loamz-cell/`](helm/loamz-cell) | The chart for Kubernetes (shared, dedicated, BYOC). |
| [`terraform/`](terraform) | `aws`, `gcp`, `azure`: network, Kubernetes, managed Postgres, object storage, IAM for credential vending. |
| [`keycloak/`](keycloak) | The realm template (imported by both compose and the chart) and a Terraform skeleton for live cells. |
| [`observability/`](observability) | Loki, Promtail, Tempo and Grafana configs carried over from the earlier stack. |
| [`PORTS.md`](PORTS.md) | The port registry. |

## Images

| Image | Dockerfile | Notes |
| --- | --- | --- |
| `loamz-agents` | `loamz-agents.Dockerfile` | `loamz-agents serve` with `--roles` support. Runs non-root; for Kubernetes code interpreter use `SANDBOX_RUNTIME=k8s`. |
| `loamz-data` | `loamz-data.Dockerfile` | Commands `serve` (:8020), `controller`, `worker --role sql\|jobs\|documents\|inference`, `attach`. |
| `loamz-agents-console` | `loamz-agents-console.Dockerfile` | Next.js standalone, :3000 under `/chat`. |
| `loamz-data-workspace` | `loamz-data-workspace.Dockerfile` | Next.js, :3011. |
| `llama-server` (+ `.gpu`) | `llama-server*.Dockerfile` | The inference runtime for embedding and reranking sidecars. |

`.github/workflows/release-images.yml` builds and pushes these to GHCR with an SBOM, provenance and a keyless cosign signature, and
pushes the chart as an OCI artifact (on a `cell-vX.Y.Z` tag or by hand).

## The four hosting models

### 1. Shared SaaS (many tenants in one cell)

The platform operator runs one large cell with the Helm chart and serves every customer as a Keycloak **organization** in the `loamz`
realm. Use `mode: microservices` when components must scale on their own, managed Postgres and object storage from a Terraform module,
`data.workers.*.autoscaling` for compute, and the `accounts` realm as a broker (`keycloak.brokering.identityProviders`).

```sh
terraform -chdir=deploy/terraform/aws apply -var name=cell-eu1 -var region=eu-west-1 -var 'browser_origins=["https://data.eu1.example.com"]'
terraform -chdir=deploy/terraform/aws output -raw helm_values > cell-eu1.values.yaml     # database, buckets, STS role, IRSA annotation
helm install cell deploy/helm/loamz-cell -n loamz --create-namespace -f cell-eu1.values.yaml -f my-urls.values.yaml \
  --set secrets.existingSecret=cell-secrets
```

### 2. Dedicated (one cell per enterprise customer)

The same chart and Terraform in the operator's account, one release per customer, with the customer's identity provider attached as an
organization identity provider in Keycloak. The control plane (private repository) provisions it through the cell-admin API.

### 3. BYOC (the customer's own cloud)

The same Terraform module is applied in the customer's account through a role they grant, then the chart is installed. Nothing in a request
path or in the data reaches the operator: the control plane only holds the `cell-admin` service account and a pull-based agent reports
health and versions. Storage credentials are vended by a role in the customer's account (`sts_vending_role_arn`, set as `s3.stsRoleArn`).

### 4. Self-hosted

Anyone, on one machine, with the compose bundle; or on any Kubernetes with the chart and the bundled Postgres/Redis/SeaweedFS.

```sh
cd deploy/compose
./gen-env.sh                                   # random secrets into .env (never committed)
docker compose --profile monolith up -d --build      # or: --profile microservices ; add --profile ml for retrieval models
```

Then open http://localhost:3000 (console) and http://localhost:3011 (data workspace). Add at least one model provider key to `.env`.
Behind a real domain, set the `*_URL` values and put TLS in front (see `Caddyfile`).

## Secrets

Nothing in this tree is a secret. Compose reads `.env` (generated; `.env*` is git-ignored). The chart reads one Secret by reference:
`secrets.existingSecret` (keys listed in `values.yaml`), or, for evaluation, generates one and keeps it across upgrades. Terraform keeps
the AWS database password in Secrets Manager; GCP and Azure put a generated password in state, so protect the state.

## Security notes

- loamz-data refuses plain HTTP and unencrypted database connections off loopback. The bundle therefore runs Postgres with TLS and the
  controller and workers over HTTPS with a private CA (`cell-init` in compose, a generated Secret in the chart). The API and the S3/Keycloak
  hops stay HTTP inside the private network and are listed in `LOAMZ_DATA_INSECURE_HTTP_HOSTS`; put TLS (ingress, mesh) in front for anything beyond one box.
- The compose agent server runs as root with `SYS_ADMIN`, `cgroup: host` and `seccomp=unconfined` because the default `nsjail` code-interpreter
  runtime needs them. The chart defaults to the `k8s` runtime and keeps every pod non-root with a read-only root filesystem.
- NetworkPolicies fence the release: only the ingress controller reaches the public components, and only the control plane reaches the controller.
