# Runbook

Use this page for the first-response workflow when something is broken.

## Fast checks

### Local backend

```bash
make ci
uv run pytest
```

### Cluster health

```bash
kubectl get pods -A
kubectl get deployments -A
```

### Logs

```bash
kubectl logs -n af-runtime deployment/agent-runtime -f
```

## Questions to answer quickly

1. Did the process start?
2. Did dependency wiring succeed?
3. Did Redis and PostgreSQL connect?
4. Did the model client or tool layer fail?
5. Did the event stream reach the UI?

## Recovery mindset

Reduce scope first. Reproduce locally in monolith mode before debugging full cluster behavior unless the bug is explicitly deployment-specific.