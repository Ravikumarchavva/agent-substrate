#!/usr/bin/env bash
# Control plane for local development: compute controller (8014), API (8020) and UI (3011).
# Requires `make up` (storage, Iceberg catalog, identity provider) first. No compute is started:
# run `make dev-compute` separately to operate and attach stand-alone workers.
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
# The control plane uses its own storage identity: catalog commits and upload signing only.
export SUBSTRATE_DATA_S3_ACCESS_KEY_ID="$CONTROL_S3_ACCESS_KEY_ID"
export SUBSTRATE_DATA_S3_SECRET_ACCESS_KEY="$CONTROL_S3_SECRET_ACCESS_KEY"
uv run substrate-data controller --port 8014 &
pids+=("$!")
uv run substrate-data serve --port 8020 &
pids+=("$!")
if [[ "${SUBSTRATE_DATA_NO_UI:-}" != "1" ]]; then
  (cd web && DATA_API_URL=http://127.0.0.1:8020 exec npm run dev) &
  pids+=("$!")
fi
wait -n "${pids[@]}"
