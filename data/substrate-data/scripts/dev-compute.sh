#!/usr/bin/env bash
# Stand-alone compute for local development, operated by you outside the application: the control
# plane only attaches to these endpoints and can never start, size or stop them. Replace this
# script with containers, VMs or a cluster in real deployments.
set -euo pipefail
cd "$(dirname "$0")/.."
[[ -f .env ]] || { echo "Run 'make up' first." >&2; exit 1; }
set -a; . ./.env; set +a
pids=()
cleanup() {
  for pid in "${pids[@]}"; do kill "$pid" 2>/dev/null || true; done
  wait || true
}
trap cleanup EXIT
trap 'exit 130' INT TERM
for entry in sql:8011 jobs:8012 rag:8013; do
  role="${entry%%:*}"; port="${entry##*:}"
  # Workers hold the worker storage identity, their own connection token and only the ticket
  # public key. They never receive the controller token or control-plane credentials.
  env -u SUBSTRATE_DATA_CONTROLLER_TOKEN -u SUBSTRATE_DATA_TICKET_PRIVATE_KEY_FILE \
    SUBSTRATE_DATA_S3_ACCESS_KEY_ID="$WORKER_S3_ACCESS_KEY_ID" \
    SUBSTRATE_DATA_S3_SECRET_ACCESS_KEY="$WORKER_S3_SECRET_ACCESS_KEY" \
    SUBSTRATE_DATA_WORKER_TOKEN="$WORKER_TOKEN" \
    uv run substrate-data --env-file /dev/null worker --role "$role" --port "$port" &
  pids+=("$!")
done
for entry in sql:8011 jobs:8012 rag:8013; do
  role="${entry%%:*}"; port="${entry##*:}"
  until curl -fs "http://127.0.0.1:$port/livez" >/dev/null; do sleep 0.3; done
  uv run substrate-data attach --name "local-$role" --role "$role" \
    --url "http://127.0.0.1:$port" --token-env WORKER_TOKEN
done
until curl -fs http://127.0.0.1:8020/livez >/dev/null; do sleep 0.5; done
uv run python scripts/grant_dev_compute.py
echo "Compute is running and attached. Ctrl-C stops these workers; the application is unaffected."
wait -n "${pids[@]}"
