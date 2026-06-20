# Observability

The repository includes a full observability stack for local cluster use.

## Components

- Loki for logs
- Tempo for traces
- Prometheus for metrics
- Grafana for dashboards
- Promtail for log shipping

## What to watch first

- backend error logs
- request latency spikes
- failed tool or model calls
- readiness probe failures
- broken SSE streams

## Common entry points

- Grafana in the Kind deployment
- structured backend logs from `setup_logging()`
- frontend warn and error logs via `/api/logs`