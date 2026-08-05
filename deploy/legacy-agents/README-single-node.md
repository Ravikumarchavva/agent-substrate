# Single-node deployment (one Linux VPS)

Runs the whole stack on one box: engine + chat UI + Postgres + Redis behind Caddy
(automatic HTTPS). Agent-generated code is isolated per session with
**bubblewrap** — Linux namespaces, no daemon, no Docker socket, no root, and no
nested virtualization (`/dev/kvm` not required), so it works on any Linux VPS.

```bash
cd deployment/docker
cp deploy.env.example deploy.env      # fill in DOMAIN, secrets, one LLM key
docker compose --env-file deploy.env -f docker-compose.deploy.yml up -d --build
```

## How code execution is isolated

`SANDBOX_RUNTIME=bubblewrap` (the default) mounts **only the caller's own session
directory** at `/workspace` inside the sandbox. Other users' directories are
absent from the mount namespace — a traversal like `../../other_user` resolves to
nothing, rather than being permission-denied. Each execution is a fresh process,
so no Python state carries between turns or between users. Network egress is
denied (`SANDBOX_NETWORK_POLICY=deny`), and the sandbox gets a minimal
environment, so host secrets (`OPENAI_API_KEY`, `JWT_SECRET`, `DATABASE_URL`)
are never visible to the model's code.

Files written to `/workspace` land directly on the durable `workspaces` volume,
so the session directory doubles as persistent storage — no sync step, and
`sandbox:` chat references plus file versioning keep working unchanged.

## Python packages available to the sandbox

With `SANDBOX_RUNTIME=bubblewrap` the sandbox executes an interpreter **on this
host** (its prefix is bind-mounted read-only), rather than a container image with
packages baked in. So the packages the tool advertises to the model — pandas,
matplotlib, openpyxl, python-docx, python-pptx, reportlab, scikit-learn, seaborn,
plotly, polars — must be importable from that interpreter. They ship in the
`sandbox` extra, which `[server]` already includes:

```bash
uv sync --extra server          # or: uv sync --extra sandbox
```

To keep them out of the engine's own environment, create a dedicated venv and
point `SANDBOX_PYTHON` at it:

```bash
uv venv /opt/sandbox-venv
uv pip install --python /opt/sandbox-venv/bin/python \
    pandas matplotlib openpyxl python-docx python-pptx reportlab \
    scikit-learn seaborn plotly polars
# then in .env:
SANDBOX_PYTHON=/opt/sandbox-venv/bin/python
```

Startup preflight runs that interpreter inside a sandbox, so a misconfigured
path fails immediately with a clear error rather than at first tool call.

## Two ways to run the backend

### A. Containerized (what the compose file does)
Simplest. Because Docker's default seccomp profile blocks
`clone(CLONE_NEWUSER)`, the backend container carries:

```yaml
cap_add: [SYS_ADMIN]
security_opt: [seccomp=unconfined]
```

**Trade-off, stated plainly:** this weakens the *outer* container boundary. The
boundary that keeps users away from each other's data is the *inner* bubblewrap
namespace, which still fully applies — but a compromise of the backend process
itself is less contained than it would otherwise be.

### B. Backend on the host via systemd (strongest posture)
Neither relaxation is needed, because there is no outer container to weaken.
Keep Postgres/Redis/Caddy in compose and run the engine directly:

```ini
# /etc/systemd/system/substrate.service
[Unit]
Description=Agent Substrate engine
After=network.target docker.service

[Service]
User=substrate
WorkingDirectory=/opt/agent-substrate
EnvironmentFile=/opt/agent-substrate/.env
ExecStart=/usr/local/bin/uv run start
Restart=always
# Defence in depth for the engine itself (the sandbox has its own boundary):
NoNewPrivileges=false     # bwrap needs to create user namespaces
PrivateTmp=true
ProtectSystem=full
ProtectHome=true

[Install]
WantedBy=multi-user.target
```

```bash
apt install bubblewrap
systemctl enable --now substrate
```

## Preflight

The engine checks bubblewrap at startup and **fails closed** with actionable
remediation rather than silently running code unisolated. If you see
`bubblewrap cannot create a namespace on this host`:

| Cause | Fix |
|---|---|
| `bwrap` missing | `apt install bubblewrap` |
| Ubuntu 24.04+ AppArmor restriction | `sysctl -w kernel.apparmor_restrict_unprivileged_userns=0` (persist in `/etc/sysctl.d/`) |
| Running in Docker without caps | add `cap_add: [SYS_ADMIN]` + `security_opt: [seccomp=unconfined]`, or use option B |

## What this deployment does not include

- **ONLYOFFICE** (editable Office files in the panel) — opt-in, ~2 GB image, and
  its container is **amd64-only**. Without it, Office files fall back to a
  read-only preview. Start with `make infra-up-onlyoffice`.
- **Docling** (OCR / rich document extraction) — heavy and GPU-oriented; the
  engine falls back to pypdf text extraction when `DOCLING_SERVICE_URL` is
  unset. Consider running it as a scale-to-zero service elsewhere instead of on
  this box.

## Residual risk

bubblewrap shares the host kernel, so a **kernel-level exploit** would escape it.
gVisor (see the Kubernetes deployment, `SANDBOX_RUNTIME=k8s` with
`SANDBOX_RUNTIME_CLASS=gvisor`) and Firecracker defend against that class;
bubblewrap does not. This is the same boundary online judges and Codex CLI
accept, and it is a large improvement over a shared sandbox with no per-user
boundary at all. Prefer the Kubernetes + gVisor path once you serve untrusted
multi-tenant traffic at scale.
