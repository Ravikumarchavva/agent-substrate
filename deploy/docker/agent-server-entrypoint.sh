#!/bin/sh
# Start the agent server in the mode the cell asks for.
#   SUBSTRATE_MODE=monolith       (default) the single API on :8000
#   SUBSTRATE_MODE=microservices  one service per container, chosen by SUBSTRATE_SERVICE
set -eu

case "${SUBSTRATE_MODE:-monolith}" in
  monolith)
    exec uvicorn substrate_cloud.monolith.app:app --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers --forwarded-allow-ips="*"
    ;;
  microservices)
    service="${SUBSTRATE_SERVICE:?SUBSTRATE_SERVICE is required in microservices mode (gateway, policy, conversation, job_controller, agent_runtime, tool_executor, human_gate, live_stream, admin)}"
    # Default ports are the ones each service documents; see deploy/PORTS.md.
    case "$service" in
      gateway) default=8001 ;;
      policy) default=8011 ;;
      conversation) default=8012 ;;
      job_controller) default=8013 ;;
      agent_runtime) default=8014 ;;
      tool_executor) default=8015 ;;
      human_gate) default=8016 ;;
      live_stream) default=8017 ;;
      admin) default=8019 ;;
      *) echo "unknown SUBSTRATE_SERVICE: $service" >&2; exit 64 ;;
    esac
    export PORT="${PORT:-$default}"
    exec uvicorn "substrate_cloud.services.${service}.app:app" --host 0.0.0.0 --port "$PORT" --proxy-headers --forwarded-allow-ips="*"
    ;;
  *)
    echo "SUBSTRATE_MODE must be monolith or microservices, got: ${SUBSTRATE_MODE}" >&2
    exit 64
    ;;
esac
