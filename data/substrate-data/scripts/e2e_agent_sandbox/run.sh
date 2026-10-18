#!/usr/bin/env bash
# End to end across a host boundary: an agent's code, in a real nsjail sandbox with no network, queries
# governed warehouse data on a separate TLS (mutual TLS, 1.3) data service and writes a chart and an Excel file.
# The agent container shares no filesystem with the data service: it holds the CA and its client certificate, and
# publishes its token-signing public keys over HTTPS for the data service to fetch.
#
# Needs (all running or built): `make up`, `make dev`, `make dev-compute`, `make demo` here; the sibling
# agent-substrate checkout with a Python environment that has its [sandbox] extra, the platform deps
# (pyjwt, cryptography) and `substrate-data/client` installed non-editable; Docker.
#
#   AGENT_SUBSTRATE   path to the agent-substrate checkout        (default ../agent-substrate)
#   AGENT_PYTHON      its Python, also the sandbox interpreter     (default $AGENT_SUBSTRATE/.venv/bin/python)
#   AGENT_VENV_DIR    that Python's venv directory                 (default $AGENT_SUBSTRATE/.venv)
set -euo pipefail
cd "$(dirname "$0")/../.."
HERE="$PWD"
AGENT="$(cd "${AGENT_SUBSTRATE:-../agent-substrate}" && pwd)"
VENV="${AGENT_VENV_DIR:-$AGENT/.venv}"
PYTHON="${AGENT_PYTHON:-$VENV/bin/python}"
E2E="$HERE/.workspace/e2e"
PORT=28020
mkdir -p "$E2E/data" "$E2E/scratch"
set -a; . ./.env; set +a

echo "== build the nsjail image (cached after the first run)"
docker build -q --target nsjail-builder -t substrate-nsjail-builder -f "$AGENT/deployment/docker/backend.Dockerfile" "$AGENT" >/dev/null
docker build -q -t substrate-e2e-nsjail "$HERE/scripts/e2e_agent_sandbox" >/dev/null

echo "== two hosts' worth of certificates (the container only ever sees agent-side/)"
rm -rf "$E2E/pki" "$E2E/data"
uv run python scripts/e2e_agent_sandbox/make_pki.py "$E2E/pki"
chmod -R a+rX "$E2E/pki"
DATA_SIDE="$E2E/pki/data-side"
KEYS_PORT=28099

echo "== a throwaway TLS API on :$PORT: mutual TLS, TLS 1.3, trusting agent-substrate by fetched keys"
export SUBSTRATE_DATA_S3_ACCESS_KEY_ID="$CONTROL_S3_ACCESS_KEY_ID" SUBSTRATE_DATA_S3_SECRET_ACCESS_KEY="$CONTROL_S3_SECRET_ACCESS_KEY"
export SUBSTRATE_DATA_TLS_CERT_FILE="$DATA_SIDE/data.crt" SUBSTRATE_DATA_TLS_KEY_FILE="$DATA_SIDE/data.key"
export SUBSTRATE_DATA_TLS_CLIENT_CA_FILE="$DATA_SIDE/ca.pem"
export SUBSTRATE_DATA_TRUSTED_ISSUERS="[{\"issuer\": \"agent-substrate\", \"jwks_uri\": \"https://127.0.0.1:$KEYS_PORT/.well-known/data-token-jwks.json\", \"jwks_ca_file\": \"$DATA_SIDE/ca.pem\"}]"
uv run substrate-data serve --host 0.0.0.0 --port "$PORT" > "$E2E/api.log" 2>&1 &
API=$!
trap 'kill $API 2>/dev/null || true' EXIT
until curl -fs --cacert "$DATA_SIDE/ca.pem" --cert "$E2E/pki/agent-side/gateway.crt" --key "$E2E/pki/agent-side/gateway.key" \
    "https://localhost:$PORT/livez" >/dev/null; do sleep 0.3; done
echo "   plaintext and certificate-less clients are refused:"
curl -s --max-time 5 "http://127.0.0.1:$PORT/livez" >/dev/null && { echo "plaintext was accepted"; exit 1; } || echo "   - plain HTTP: refused"
curl -s --max-time 5 --cacert "$DATA_SIDE/ca.pem" "https://localhost:$PORT/livez" >/dev/null && { echo "no-cert client was accepted"; exit 1; } || echo "   - no client certificate: refused"

echo "== grant tenant 'acme' the three sample views, but not the raw table"
uv run python - <<'PY'
import os
from dotenv import dotenv_values
from substrate_data_client import ClientCredentials, DataClient
env = {**dotenv_values(".env"), **os.environ}
c = DataClient("http://127.0.0.1:8020", token=ClientCredentials(env["SUBSTRATE_DATA_OIDC_ISSUER"], "substrate-data-bench", env["BENCH_CLIENT_SECRET"]))
c.request("PATCH", "/grants/schema/main.nyc", json={"changes": [{"principal": "group:tenant-acme", "add": ["USE_SCHEMA"]}]})
for view in ("monthly_revenue", "busiest_zones_august", "long_trips"):
    c.request("PATCH", f"/grants/view/main.nyc.{view}", json={"changes": [{"principal": "group:tenant-acme", "add": ["SELECT"]}]})
PY

echo "== run the agent's code in a real sandbox"
UV_PYTHON_DIR="$(dirname "$(dirname "$(dirname "$(readlink -f "$PYTHON")")")")"
# The container runs as root (nsjail needs it); hand the output files back to the invoking user afterwards.
docker run --rm --cgroupns=host --cap-add SYS_ADMIN --security-opt seccomp=unconfined \
  --security-opt apparmor=unconfined -v /sys/fs/cgroup:/sys/fs/cgroup:rw \
  --add-host=host.docker.internal:host-gateway -p "127.0.0.1:$KEYS_PORT:$KEYS_PORT" \
  -v "$E2E/pki/agent-side:/opt/agent-side:ro" \
  -v "$UV_PYTHON_DIR:$UV_PYTHON_DIR:ro" -v "$VENV:/opt/venv:ro" -v "$E2E:/opt/e2e" -v "$AGENT:$AGENT:ro" \
  -v "$HERE/scripts/e2e_agent_sandbox:/opt/harness:ro" \
  -e PYTHONPATH="$AGENT/apps/substrate-cloud/src:$AGENT/src" -e PYTHONDONTWRITEBYTECODE=1 \
  --entrypoint /bin/bash substrate-e2e-nsjail -c \
  "/opt/venv/bin/python /opt/harness/harness.py /opt/e2e /opt/venv/bin/python https://host.docker.internal:$PORT /opt/agent-side $KEYS_PORT; \
   status=\$?; chown -R $(id -u):$(id -g) /opt/e2e; exit \$status"
echo "== files: $E2E/scratch/e2e-thread/main/"
ls -la "$E2E/scratch/e2e-thread/main/"
