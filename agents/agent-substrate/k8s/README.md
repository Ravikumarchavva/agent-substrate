# Kubernetes Deployment Guide

This directory contains production-ready Kubernetes manifests for deploying the AI Chatbot with Agent Framework.

## Architecture

```
┌─────────────────────────────────────┐
│     Ingress (nginx)                 │
│     HTTPS (443)                     │
└──────────────┬──────────────────────┘
               │
        ┌──────┴──────┐
        │             │
  ┌─────▼──────┐ ┌───▼──────────────┐
  │  Frontend  │ │    Backend       │
  │  (Next.js) │ │    (FastAPI)     │
  │  3 replicas│ │    5 replicas    │
  └─────┬──────┘ └───┬──────────────┘
        │            │
        └──────┬─────┘
               │
      ┌────────▼────────┐
      │   PostgreSQL    │
      │   StatefulSet   │
      │   (Primary +    │
      │    2 Replicas)  │
```

## Files

- `namespace.yaml` - Dedicated namespace for the application
- `configmap.yaml` - Non-sensitive configuration
- `secrets.yaml` - Sensitive credentials (OAuth keys, encryption keys)
- `postgres-statefulset.yaml` - PostgreSQL database with persistent storage
- `postgres-service.yaml` - ClusterIP service for database
- `backend-deployment.yaml` - Python FastAPI backend (agent framework)
- `backend-service.yaml` - ClusterIP service for backend
- `frontend-deployment.yaml` - Next.js frontend
- `frontend-service.yaml` - ClusterIP service for frontend
- `ingress.yaml` - Nginx ingress for external access
- `hpa.yaml` - HorizontalPodAutoscaler for auto-scaling
- `pvc.yaml` - PersistentVolumeClaims for database storage

## Prerequisites

1. **Kubernetes cluster** (EKS, GKE, AKS, or local with kind/minikube)
2. **kubectl** configured
3. **Ingress controller** (nginx-ingress)
4. **Cert-manager** (for TLS certificates)
5. **Container registry** (ECR, GCR, Docker Hub)

## Setup Instructions

### 1. Build and Push Docker Images

```bash
# Build frontend
cd ai-chatbot-ui
docker build -t your-registry/agent-frontend:latest .
docker push your-registry/agent-frontend:latest

# Build backend
cd ../agent-framework
docker build -t your-registry/agent-backend:latest .
docker push your-registry/agent-backend:latest
```

### 2. Update Secrets

```bash
# Generate encryption key
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# Edit secrets.yaml and replace placeholders
kubectl create secret generic agent-secrets \
  --from-literal=db-password='your-db-password' \
  --from-literal=encryption-key='your-encryption-key' \
  --from-literal=nextauth-secret='your-nextauth-secret' \
  --from-literal=openai-api-key='sk-...' \
  --from-literal=spotify-client-id='your-spotify-client-id' \
  --from-literal=spotify-client-secret='your-spotify-client-secret' \
  --from-literal=google-client-id='your-google-client-id' \
  --from-literal=google-client-secret='your-google-client-secret' \
  -n agent-framework
```

### 3. Deploy to Kubernetes

```bash
# Create namespace
kubectl apply -f namespace.yaml

# Create secrets & configmaps
kubectl apply -f configmap.yaml
kubectl apply -f secrets.yaml

# Deploy database
kubectl apply -f pvc.yaml
kubectl apply -f postgres-statefulset.yaml
kubectl apply -f postgres-service.yaml

# Wait for database to be ready
kubectl wait --for=condition=ready pod -l app=postgres -n agent-framework --timeout=300s

# Run database migrations
kubectl run -it --rm migration \
  --image=your-registry/agent-frontend:latest \
  --restart=Never \
  -n agent-framework \
  -- pnpm prisma migrate deploy

# Deploy backend
kubectl apply -f backend-deployment.yaml
kubectl apply -f backend-service.yaml

# Deploy frontend
kubectl apply -f frontend-deployment.yaml
kubectl apply -f frontend-service.yaml

# Deploy ingress
kubectl apply -f ingress.yaml

# Deploy autoscalers
kubectl apply -f hpa.yaml
```

### 4. Verify Deployment

```bash
# Check all pods are running
kubectl get pods -n agent-framework

# Check services
kubectl get svc -n agent-framework

# Check ingress
kubectl get ingress -n agent-framework

# View logs
kubectl logs -f deployment/agent-backend -n agent-framework
kubectl logs -f deployment/agent-frontend -n agent-framework
```

### 5. Access Application

Get the ingress IP/hostname:
```bash
kubectl get ingress agent-ingress -n agent-framework
```

Add DNS record pointing to the ingress IP, then access:
```
https://your-domain.com
```

## TLS/SSL Setup

### Using cert-manager (Recommended)

1. Install cert-manager:
```bash
kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/v1.13.0/cert-manager.yaml
```

2. Create ClusterIssuer:
```yaml
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: letsencrypt-prod
spec:
  acme:
    server: https://acme-v02.api.letsencrypt.org/directory
    email: your-email@example.com
    privateKeySecretRef:
      name: letsencrypt-prod
    solvers:
    - http01:
        ingress:
          class: nginx
```

3. Update ingress.yaml to use cert-manager:
```yaml
annotations:
  cert-manager.io/cluster-issuer: "letsencrypt-prod"
```

## Monitoring

### Install Prometheus & Grafana

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm install prometheus prometheus-community/kube-prometheus-stack -n monitoring --create-namespace
```

### Access Grafana

```bash
kubectl port-forward svc/prometheus-grafana 3000:80 -n monitoring
# Username: admin, Password: prom-operator
```

## Scaling

### Manual Scaling

```bash
# Scale backend
kubectl scale deployment agent-backend --replicas=10 -n agent-framework

# Scale frontend
kubectl scale deployment agent-frontend --replicas=5 -n agent-framework
```

### Auto-scaling (HPA)

The HPA automatically scales based on CPU/memory:
- Backend: 3-20 replicas (70% CPU target)
- Frontend: 2-10 replicas (70% CPU target)

## Backup & Recovery

### Database Backup

```bash
# Create backup
kubectl exec -it postgres-0 -n agent-framework -- \
  pg_dump -U agent agent_framework > backup-$(date +%Y%m%d).sql

# Restore backup
kubectl exec -i postgres-0 -n agent-framework -- \
  psql -U agent agent_framework < backup-20240217.sql
```

### Automated Backups

Use a CronJob:
```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: postgres-backup
  namespace: agent-framework
spec:
  schedule: "0 2 * * *"  # Daily at 2 AM
  jobTemplate:
    spec:
      template:
        spec:
          containers:
          - name: postgres-backup
            image: postgres:16-alpine
            command:
            - /bin/sh
            - -c
            - pg_dump -h postgres -U agent agent_framework | gzip > /backup/backup-$(date +%Y%m%d).sql.gz
            volumeMounts:
            - name: backup
              mountPath: /backup
          volumes:
          - name: backup
            persistentVolumeClaim:
              claimName: postgres-backup-pvc
          restartPolicy: OnFailure
```

## Troubleshooting

### Pods not starting

```bash
kubectl describe pod <pod-name> -n agent-framework
kubectl logs <pod-name> -n agent-framework
```

### Database connection errors

```bash
# Check database is running
kubectl get pods -l app=postgres -n agent-framework

# Test connection
kubectl exec -it postgres-0 -n agent-framework -- psql -U agent agent_framework
```

### OAuth not working

1. Check secrets are set:
```bash
kubectl get secret agent-secrets -n agent-framework -o yaml
```

2. Verify redirect URIs in OAuth provider match ingress hostname
3. Check logs for errors:
```bash
kubectl logs deployment/agent-frontend -n agent-framework | grep -i oauth
```

## Cost Optimization

### Development Environment

```yaml
# Reduce replicas
Backend: 1 replica
Frontend: 1 replica
Database: Single node (no replicas)

# Use smaller instance types
Node pool: t3.small (2 vCPU, 2GB RAM)

Estimated cost: $50-100/month
```

### Production Environment

```yaml
# High availability
Backend: 3-10 replicas
Frontend: 2-5 replicas
Database: 1 primary + 2 read replicas

# Production instance types
Node pool: t3.large (2 vCPU, 8GB RAM) x 3-6 nodes

Estimated cost: $300-800/month
```

## Security Checklist

- [ ] Secrets stored in Kubernetes secrets (not in git)
- [ ] HTTPS enforced via ingress
- [ ] Network policies configured
- [ ] Pod security policies enabled
- [ ] RBAC configured for least privilege
- [ ] Database encrypted at rest
- [ ] Regular security updates
- [ ] Vulnerability scanning enabled
- [ ] Rate limiting configured in ingress
- [ ] WAF enabled (if using cloud provider)

## CI/CD Integration

See `../ci-cd/` directory for:
- GitHub Actions workflows
- GitLab CI/CD pipelines
- ArgoCD configuration
- Flux CD configuration

## Support

For issues:
1. Check logs: `kubectl logs <pod-name> -n agent-framework`
2. Check events: `kubectl get events -n agent-framework`
3. Check ingress: `kubectl describe ingress agent-ingress -n agent-framework`
4. Review [TROUBLESHOOTING.md](../TROUBLESHOOTING.md)
