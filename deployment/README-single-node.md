# Single-node deployment (one Linux VPS)

Runs the whole stack on one box: engine + chat UI + Postgres + Redis behind Caddy
(automatic HTTPS). Agent-generated code is isolated per session with
**nsjail** — Linux namespaces + cgroups, no daemon, no Docker socket, no root,
and no nested virtualization (`/dev/kvm` not required), so it works on any
Linux VPS. Unlike a plain namespace sandbox, nsjail also enforces a real
per-sandbox process-count/memory cap via cgroups.

```bash
cd deployment/docker
cp deploy.env.example deploy.env      # fill in DOMAIN, secrets, one LLM key
docker compose --env-file deploy.env -f docker-compose.deploy.yml up -d --build
```

## How code execution is isolated

`SANDBOX_RUNTIME=nsjail` (the default) mounts **only the caller's own session
directory** at `/workspace` inside the sandbox. Other users' directories are
absent from the mount namespace — a traversal like `../../other_user` resolves to
nothing, rather than being permission-denied. Each execution is a fresh process,
so no Python state carries between turns or between users. A cgroup process-count
cap (`--cgroup_pids_max`, 64 by default) contains fork bombs; network egress is
denied (`SANDBOX_NETWORK_POLICY=deny`), and the sandbox gets a minimal
environment, so host secrets (`OPENAI_API_KEY`, `JWT_SECRET`, `DATABASE_URL`)
are never visible to the model's code.

Files written to `/workspace` land directly on the durable `workspaces` volume,
so the session directory doubles as persistent storage — no sync step, and
`sandbox:` chat references plus file versioning keep working unchanged.

## Python packages available to the sandbox

With `SANDBOX_RUNTIME=nsjail` the sandbox executes an interpreter **on this
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
Simplest. nsjail needs two separate things inside the container: unprivileged
user+mount namespaces (Docker's default seccomp profile blocks
`clone(CLONE_NEWUSER)`) and ownership of a cgroup v2 subtree to enforce its
process/memory caps. The compose file grants both:

```yaml
user: root
cgroupns_mode: host
cap_add: [SYS_ADMIN]
security_opt: [seccomp=unconfined]
```

**Trade-off, stated plainly:** `cgroupns_mode: host` gives the container the
host's real cgroup tree, and only `root` owns that tree outright without
further per-user delegation plumbing — a non-root container user has no
cgroup v2 subtree delegated to it here (there's no systemd running inside
the container to delegate one), so nsjail's own preflight would otherwise
fail closed at startup. Running the backend container as root weakens the
*outer* container boundary more than the previous non-root setup did. The
boundary that keeps users away from each other's data is still the *inner*
nsjail namespace, which fully applies regardless of the outer container's
uid — but a compromise of the backend process itself is less contained than
it would be non-root. Option B avoids this trade-off entirely.

### B. Backend on the host via systemd (strongest posture)
No outer container to weaken, and systemd delegates your own unprivileged
user a real cgroup v2 subtree automatically — so neither the container
relaxations above nor running as root are needed. Keep Postgres/Redis/Caddy
in compose and run the engine directly:

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
# Defence in depth for the engine itself (the sandbox has its own boundary):
NoNewPrivileges=false     # nsjail needs to create user namespaces
PrivateTmp=true
ProtectSystem=full
ProtectHome=true
# Delegates a cgroup v2 subtree to this service's own slice (cpu/memory/pids)
# — required for nsjail's process-count/memory caps to initialize. Present
# by default on any systemd >= 245 host; explicit here for clarity.
Delegate=cpu memory pids

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

## Preflight

The engine checks nsjail at startup and **fails closed** with actionable
remediation rather than silently running code unisolated. If you see
`nsjail cannot create a sandbox on this host` or `No usable cgroup v2 subtree`:

| Cause | Fix |
|---|---|
| `nsjail` missing | build it — see the systemd section above (no apt package exists) |
| Running unprivileged with no cgroup delegation | run under a systemd unit with `Delegate=cpu memory pids` (see above), or `systemctl edit user@$(id -u).service` and add the same under `[Service]`, then re-login |
| Running in Docker without cgroup delegation | add `cgroupns_mode: host` + `user: root` (see option A above), or use option B |
| Running in Docker without namespace caps | add `cap_add: [SYS_ADMIN]` + `security_opt: [seccomp=unconfined]`, or use option B |

## What this deployment does not include

- **Docling** (OCR / rich document extraction) — heavy and GPU-oriented; the
  engine falls back to pypdf text extraction when `DOCLING_SERVICE_URL` is
  unset. Consider running it as a scale-to-zero service elsewhere instead of on
  this box.

## Residual risk

nsjail shares the host kernel, so a **kernel-level exploit** would escape it.
gVisor (see the Kubernetes deployment, `SANDBOX_RUNTIME=k8s` with
`SANDBOX_RUNTIME_CLASS=gvisor`) and Firecracker defend against that class;
nsjail does not. This is the same class of boundary online judges and Codex
CLI accept, and it is a large improvement over a shared sandbox with no
per-user boundary at all. Prefer the Kubernetes + gVisor path once you serve
untrusted multi-tenant traffic at scale.
