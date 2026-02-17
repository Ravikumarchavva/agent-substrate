#!/bin/bash
# Kubernetes Deployment Script for Agent Framework

set -e

# Configuration
NAMESPACE="agent-framework"
REGISTRY=""
BACKEND_IMAGE="agent-backend:latest"
FRONTEND_IMAGE="agent-frontend:latest"

echo "🚀 Deploying Agent Framework to Kubernetes"
echo "==========================================="
echo "Namespace: $NAMESPACE"
echo "Backend Image: $BACKEND_IMAGE"
echo "Frontend Image: $FRONTEND_IMAGE"
echo ""

# Check if kubectl is installed
if ! command -v kubectl &> /dev/null; then
    echo "❌ kubectl is not installed. Please install kubectl first."
    exit 1
fi

# Check kubectl connectivity
if ! kubectl cluster-info &> /dev/null; then
    echo "❌ Cannot connect to Kubernetes cluster. Check your kubeconfig."
    exit 1
fi

echo "✅ Connected to Kubernetes cluster"
echo ""

# Build Docker images
echo "📦 Building Docker images..."
echo "Building backend..."
docker build -t $BACKEND_IMAGE -f Dockerfile .

echo "Building frontend..."
cd ../ai-chatbot-ui
docker build -t $FRONTEND_IMAGE -f Dockerfile .

cd ..

# Push images
# echo "⬆️  Pushing images to registry..."
# docker push $BACKEND_IMAGE
# docker push $FRONTEND_IMAGE

echo "✅ Images built locally"
echo ""

# Create namespace
echo "📁 Creating namespace..."
kubectl apply -f agent-framework/k8s/namespace.yaml

# Check if secrets exist
if kubectl get secret agent-secrets -n $NAMESPACE &> /dev/null; then
    echo "⚠️  Secrets already exist. Skipping secret creation."
    echo "   To update secrets, manually edit or delete and recreate."
else
    echo "🔐 Creating secrets..."
    echo "⚠️  WARNING: The secrets.yaml contains placeholder values."
    echo "   Please update them before deploying to production!"
    read -p "Continue with placeholder secrets? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Deployment cancelled. Please update secrets.yaml and run again."
        exit 1
    fi
    kubectl apply -f agent-framework/k8s/secrets.yaml
fi

# Apply ConfigMaps
echo "⚙️  Applying ConfigMaps..."
kubectl apply -f agent-framework/k8s/configmap.yaml

# Deploy PostgreSQL
echo "🗄️  Deploying PostgreSQL..."
kubectl apply -f agent-framework/k8s/pvc.yaml
kubectl apply -f agent-framework/k8s/postgres-statefulset.yaml
kubectl apply -f agent-framework/k8s/postgres-service.yaml

# Wait for PostgreSQL to be ready
echo "⏳ Waiting for PostgreSQL to be ready..."
kubectl wait --for=condition=ready pod -l app=postgres -n $NAMESPACE --timeout=300s

echo "✅ PostgreSQL is ready"
echo ""

# Run database migrations (optional - skip if DB already initialized)
# echo "🔄 Running database migrations..."
# kubectl run -it --rm migration \
#     --image=$FRONTEND_IMAGE \
#     --restart=Never \
#     -n $NAMESPACE \
#     --command -- sh -c "pnpm prisma migrate deploy"
# 
# echo "✅ Migrations completed"
# echo ""

# Deploy Backend
echo "🖥️  Deploying backend..."
kubectl apply -f agent-framework/k8s/backend-deployment.yaml

# Deploy Frontend
echo "💻 Deploying frontend..."
kubectl apply -f agent-framework/k8s/frontend-deployment.yaml

# Wait for deployments to be ready
echo "⏳ Waiting for deployments to be ready..."
kubectl wait --for=condition=available deployment/agent-backend -n $NAMESPACE --timeout=300s
kubectl wait --for=condition=available deployment/agent-frontend -n $NAMESPACE --timeout=300s

echo "✅ Deployments are ready"
echo ""

# Deploy Ingress
echo "🌐 Deploying ingress..."
kubectl apply -f agent-framework/k8s/ingress.yaml

# Deploy HPA
echo "📈 Deploying auto-scalers..."
kubectl apply -f agent-framework/k8s/hpa.yaml

echo ""
echo "✅ Deployment completed successfully!"
echo ""

# Show deployment status
echo "📊 Deployment Status"
echo "===================="
kubectl get pods -n $NAMESPACE
echo ""
kubectl get svc -n $NAMESPACE
echo ""
kubectl get ingress -n $NAMESPACE
echo ""

# Get ingress URL
INGRESS_IP=$(kubectl get ingress agent-ingress -n $NAMESPACE -o jsonpath='{.status.loadBalancer.ingress[0].ip}')
INGRESS_HOSTNAME=$(kubectl get ingress agent-ingress -n $NAMESPACE -o jsonpath='{.status.loadBalancer.ingress[0].hostname}')

echo "🌍 Access your application at:"
if [ -n "$INGRESS_IP" ]; then
    echo "   IP: https://$INGRESS_IP"
fi
if [ -n "$INGRESS_HOSTNAME" ]; then
    echo "   Hostname: https://$INGRESS_HOSTNAME"
fi
echo ""

echo "📝 Next steps:"
echo "1. Point your domain DNS to the ingress IP/hostname"
echo "2. Update configmap.yaml with your domain"
echo "3. Update ingress.yaml with your domain"
echo "4. Update secrets with production values"
echo "5. Configure OAuth redirect URIs in Spotify/Google dashboards"
echo ""
echo "📚 For more information, see: agent-framework/k8s/README.md"
