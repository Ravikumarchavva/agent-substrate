# Agent Sandbox manifests for this project

These manifests are checked into the repo so cluster setup is reproducible and reviewable.

## One-time install (controller + extensions)

```bash
kubectl apply -f https://github.com/kubernetes-sigs/agent-sandbox/releases/download/v0.4.5/manifest.yaml
kubectl apply -f https://github.com/kubernetes-sigs/agent-sandbox/releases/download/v0.4.5/extensions.yaml
```

## Deploy router and template from this repo

```bash
kubectl apply -f deploy/agent-sandbox/sandbox-router.yaml
kubectl apply -f deploy/agent-sandbox/sandboxtemplate.code-interpreter.yaml
```

## Build and load image into kind

```bash
docker build -t code-interpreter:latest .
kind load docker-image code-interpreter:latest --name ai-lab
```

## Verify

```bash
kubectl get deploy,pod,svc -n default | grep sandbox-router
kubectl get sandboxtemplate -n default
```

## Cleanup (remove what this repo created)

```bash
kubectl delete -f deploy/agent-sandbox/code-interpreter-data-api.yaml --ignore-not-found
kubectl delete -f deploy/agent-sandbox/sandbox-router.yaml --ignore-not-found
kubectl delete -f deploy/agent-sandbox/sandboxtemplate.code-interpreter.yaml --ignore-not-found
```

Quick check after cleanup:

```bash
kubectl get deploy,svc,sandboxtemplate -n default \
	| grep -E 'code-interpreter-data-api|sandbox-router|python-sandbox-template' || true
```

## Recreate full setup

1. Build and load image into kind:

```bash
docker build -t code-interpreter:latest .
kind load docker-image code-interpreter:latest --name ai-lab
```

2. Ensure controller and extensions are installed:

```bash
kubectl apply -f https://github.com/kubernetes-sigs/agent-sandbox/releases/download/v0.4.5/manifest.yaml
kubectl apply -f https://github.com/kubernetes-sigs/agent-sandbox/releases/download/v0.4.5/extensions.yaml
```

3. Apply project manifests:

```bash
kubectl apply -f deploy/agent-sandbox/sandbox-router.yaml
kubectl apply -f deploy/agent-sandbox/sandboxtemplate.code-interpreter.yaml
kubectl apply -f deploy/agent-sandbox/code-interpreter-data-api.yaml
```

4. Verify everything is ready:

```bash
kubectl get deploy,svc,sandboxtemplate -n default \
	| grep -E 'code-interpreter-data-api|sandbox-router|python-sandbox-template'
```
