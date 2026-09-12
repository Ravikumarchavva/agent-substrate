# Deployment

One Docker Compose file (`deployment/docker/docker-compose.yml`), three ways to use it:

| You want to... | Command |
|---|---|
| **Develop** — infra in Docker, backend hot-reloading on your host | `make infra-up` then `uv run substrate start --reload` |
| **One-shot run** — the whole app in containers, nothing installed locally | `make docker-up` (or add `--profile ui` for the chat UI too) |
| **Deploy** — a real internet-facing box with HTTPS | see [Deploy publicly](#deploy-publicly) below |

All three use the same file — no separate compose file per mode, no Kubernetes manifests to keep in sync with it. Profiles select what comes up: infra services (Postgres, Redis, SeaweedFS, observability, MCP server) and `backend` always start; `substrate-ui` and `caddy` are opt-in via `--profile ui` / `--profile deploy`.

```bash
cd deployment/docker
cp deploy.env.example ../../.env   # if you don't already have one — fill in at least one LLM key
```

## Develop

```bash
make infra-up          # Postgres, Redis, SeaweedFS, observability, MCP server
uv run substrate start --reload
```

This is the fast loop: the backend runs on your host with hot-reload, everything else is in Docker. See the root `CLAUDE.md` for the full command list (`make lint`, `make test`, etc.).

## One-shot run

```bash
make docker-up                        # backend + infra, fully containerized
# or, with the chat UI too:
docker compose --env-file ../../.env -f docker-compose.yml --profile ui up -d --build
```

Backend on `:8000`, chat UI (if included) on `:3000`. No local Python/Node install needed — everything builds and runs in containers.

## How code execution is isolated

`SANDBOX_RUNTIME=nsjail` (the default) mounts **only the caller's own session
directory** at `/workspace` inside the sandbox — other users' directories are
absent from the mount namespace, not merely permission-denied, so a traversal
like `../../other_user` resolves to nothing. A cgroup process-count cap
(`--cgroup_pids_max`, 64 by default) contains fork bombs — a real per-sandbox
limit, which `RLIMIT_NPROC` alone cannot provide (it caps per-UID
system-wide). Network egress is denied by default
(`SANDBOX_NETWORK_POLICY=deny`), and the sandbox gets a minimal environment,
so host secrets (`OPENAI_API_KEY`, `JWT_SECRET`, `DATABASE_URL`) are never
visible to the model's code. Files written to `/workspace` land directly on
the durable `workspaces` volume — no sync step.

nsjail needs the `nsjail` binary on `PATH` and a usable cgroup v2 subtree.
The `backend` service in `docker-compose.yml` already carries what that
needs inside a container (`user: root`, `cgroup: host`, `cap_add:
[SYS_ADMIN]`, `security_opt: [seccomp=unconfined]`) — see the comment above
that service for the trade-off this implies, and
[Strongest posture](#strongest-posture-systemd-on-the-host) below for the
alternative that avoids it.

### Python packages available to the sandbox

The sandbox executes an interpreter **on the backend container itself**
(its prefix is bind-mounted read-only into the jail), not a separate image
with packages baked in. `pandas`, `matplotlib`, `openpyxl`, `python-docx`,
`python-pptx`, `reportlab`, `scikit-learn`, `seaborn`, `plotly`, `polars`
ship in the `sandbox` extra, already included in the backend image's
`[server]` install. Point `SANDBOX_PYTHON` at a different interpreter to
keep those packages out of the engine's own environment instead.

### Preflight

The engine checks nsjail at startup and **fails closed** with actionable
remediation rather than silently running code unisolated:

| Error | Cause | Fix |
|---|---|---|
| `nsjail cannot create a sandbox on this host` | `nsjail` missing | build it — see [Strongest posture](#strongest-posture-systemd-on-the-host) below (no apt package exists) |
| `No usable cgroup v2 subtree` (unprivileged) | no cgroup delegation | run under systemd with `Delegate=cpu memory pids` (see below), or run in Docker with `cgroup: host` + `user: root` (already set in `docker-compose.yml`) |
| `Couldn't setup parent cgroup` (in Docker) | missing `cgroup: host` | already set on `backend` in `docker-compose.yml`; check it wasn't overridden |
| mount/namespace errors in Docker | missing capabilities | already set (`cap_add: [SYS_ADMIN]`, `security_opt: [seccomp=unconfined]`) — check they weren't overridden |

### Strongest posture: systemd on the host

Running the backend as a systemd unit avoids the container privilege
trade-off entirely — systemd delegates your own unprivileged user a real
cgroup v2 subtree automatically, so `user: root` / `cgroup: host` /
`cap_add` / `seccomp=unconfined` aren't needed at all. Keep
Postgres/Redis/etc. in Compose and run the engine directly:

```ini
# /etc/systemd/system/substrate.service
[Unit]
Description=Agent Substrate engine
After=network.target docker.service

[Service]
User=substrate
WorkingDirectory=/opt/agent-substrate
EnvironmentFile=/opt/agent-substrate/.env
ExecStart=/usr/local/bin/uv run substrate start --host 0.0.0.0 --foreground
Restart=always
NoNewPrivileges=false     # nsjail needs to create user namespaces
PrivateTmp=true
ProtectSystem=full
ProtectHome=true
Delegate=cpu memory pids  # cgroup subtree for nsjail's process/memory caps

[Install]
WantedBy=multi-user.target
```

Build nsjail (no apt/Debian package exists — see github.com/google/nsjail):

```bash
sudo apt-get install -y autoconf bison flex gcc g++ git \
    libprotobuf-dev libnl-route-3-dev libtool make pkg-config protobuf-compiler
git clone --branch 3.6 --depth 1 https://github.com/google/nsjail.git
cd nsjail && git submodule update --init --recursive && make
sudo install -m 755 nsjail /usr/local/bin/nsjail
```

```bash
systemctl enable --now substrate
```

### Residual risk

nsjail shares the host kernel, so a **kernel-level exploit** would escape
it. This is the same class of boundary online judges and Codex CLI accept,
and it's a large improvement over a shared sandbox with no per-user
boundary at all — but if you need to defend against a kernel exploit
specifically, run the sandboxed code interpreter on gVisor or Firecracker
instead (`SANDBOX_RUNTIME=k8s` with a gVisor `RuntimeClass`, on your own
cluster — not covered by this compose file).

## Deploy publicly

Same file, plus Caddy for automatic HTTPS. Fill in the secrets, point your
domain's A record at the box, then:

```bash
cd deployment/docker
cp deploy.env.example deploy.env      # fill in DOMAIN, secrets, one LLM key
docker compose --env-file deploy.env -f docker-compose.yml --profile ui --profile deploy up -d --build
```

Caddy fetches a real TLS cert for `$DOMAIN` automatically on first boot —
see `Caddyfile`. `--profile deploy` adds only `caddy`; `--profile ui` (still
required) adds the chat UI it fronts.

**Firewall note**: this compose file publishes Postgres (`5432`) and Redis
(`6379`) to the host for local development convenience. On a public box,
make sure your cloud provider's firewall/security group blocks inbound
access to those ports — Docker's own port publishing bypasses a host-level
UFW/iptables `DROP` rule by inserting its own `nat` rules, so the firewall
needs to block at the cloud-provider level, not just the OS level.

## What this doesn't include

- **Docling** (OCR / rich document extraction) — heavy and GPU-oriented;
  the engine falls back to pypdf text extraction when
  `DOCUMENT_INTELLIGENCE_SERVICE_URL` is unset. `--profile
  document-intelligence` brings up the lighter CPU-only `document-intelligence`
  service if you want it; see `docker-compose.yml`'s own comments for the
  GPU variant and the `embedding-reranker`/`llama-gpu` profiles.
- **Horizontal scaling** — this is a single-box deployment (one backend
  container, one Postgres, one Redis). The engine's microservices split
  (`src/substrate/serving/services/`) exists in code for anyone who needs
  to scale a specific service independently on their own infrastructure,
  but this repo no longer ships prebuilt Compose/Kubernetes manifests for
  it — build your own from the service list in `CLAUDE.md`'s
  "Microservices — Roles & ORM Models" table if you need that.
