# Kubernetes Deployment Script for Agent Framework (PowerShell)

$ErrorActionPreference = "Stop"

# Configuration
$NAMESPACE = "agent-framework"
$REGISTRY = if ($env:DOCKER_REGISTRY) { $env:DOCKER_REGISTRY } else { "your-registry.com" }
$BACKEND_IMAGE = "$REGISTRY/agent-backend:latest"
$FRONTEND_IMAGE = "$REGISTRY/agent-frontend:latest"

Write-Host "🚀 Deploying Agent Framework to Kubernetes" -ForegroundColor Cyan
Write-Host "==========================================="
Write-Host "Namespace: $NAMESPACE"
Write-Host "Backend Image: $BACKEND_IMAGE"
Write-Host "Frontend Image: $FRONTEND_IMAGE"
Write-Host ""

# Check if kubectl is installed
if (!(Get-Command kubectl -ErrorAction SilentlyContinue)) {
    Write-Host "❌ kubectl is not installed. Please install kubectl first." -ForegroundColor Red
    exit 1
}

# Check kubectl connectivity
$clusterInfo = kubectl cluster-info 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ Cannot connect to Kubernetes cluster. Check your kubeconfig." -ForegroundColor Red
    exit 1
}

Write-Host "✅ Connected to Kubernetes cluster" -ForegroundColor Green
Write-Host ""

# Build Docker images
Write-Host "📦 Building Docker images..." -ForegroundColor Yellow
Write-Host "Building backend..."
Push-Location agent-framework
docker build -t $BACKEND_IMAGE -f Dockerfile .
if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ Backend build failed" -ForegroundColor Red
    Pop-Location
    exit 1
}

Write-Host "Building frontend..."
Pop-Location
Push-Location ai-chatbot-ui
docker build -t $FRONTEND_IMAGE -f Dockerfile .
if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ Frontend build failed" -ForegroundColor Red
    Pop-Location
    exit 1
}
Pop-Location

# Push images
Write-Host "⬆️  Pushing images to registry..." -ForegroundColor Yellow
docker push $BACKEND_IMAGE
docker push $FRONTEND_IMAGE

Write-Host "✅ Images pushed successfully" -ForegroundColor Green
Write-Host ""

# Create namespace
Write-Host "📁 Creating namespace..." -ForegroundColor Yellow
kubectl apply -f agent-framework/k8s/namespace.yaml

# Check if secrets exist
$secretExists = kubectl get secret agent-secrets -n $NAMESPACE 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host "⚠️  Secrets already exist. Skipping secret creation." -ForegroundColor Yellow
    Write-Host "   To update secrets, manually edit or delete and recreate."
} else {
    Write-Host "🔐 Creating secrets..." -ForegroundColor Yellow
    Write-Host "⚠️  WARNING: The secrets.yaml contains placeholder values." -ForegroundColor Yellow
    Write-Host "   Please update them before deploying to production!"
    $reply = Read-Host "Continue with placeholder secrets? (y/N)"
    if ($reply -notmatch "^[Yy]$") {
        Write-Host "Deployment cancelled. Please update secrets.yaml and run again." -ForegroundColor Yellow
        exit 1
    }
    kubectl apply -f agent-framework/k8s/secrets.yaml
}

# Apply ConfigMaps
Write-Host "⚙️  Applying ConfigMaps..." -ForegroundColor Yellow
kubectl apply -f agent-framework/k8s/configmap.yaml

# Deploy PostgreSQL
Write-Host "🗄️  Deploying PostgreSQL..." -ForegroundColor Yellow
kubectl apply -f agent-framework/k8s/pvc.yaml
kubectl apply -f agent-framework/k8s/postgres-statefulset.yaml
kubectl apply -f agent-framework/k8s/postgres-service.yaml

# Wait for PostgreSQL to be ready
Write-Host "⏳ Waiting for PostgreSQL to be ready..." -ForegroundColor Yellow
kubectl wait --for=condition=ready pod -l app=postgres -n $NAMESPACE --timeout=300s

Write-Host "✅ PostgreSQL is ready" -ForegroundColor Green
Write-Host ""

# Run database migrations
Write-Host "🔄 Running database migrations..." -ForegroundColor Yellow
kubectl run migration --image=$FRONTEND_IMAGE --restart=Never -n $NAMESPACE --command -- sh -c "pnpm prisma migrate deploy"
Start-Sleep -Seconds 30  # Give it time to complete
kubectl delete pod migration -n $NAMESPACE

Write-Host "✅ Migrations completed" -ForegroundColor Green
Write-Host ""

# Deploy Backend
Write-Host "🖥️  Deploying backend..." -ForegroundColor Yellow
kubectl apply -f agent-framework/k8s/backend-deployment.yaml

# Deploy Frontend
Write-Host "💻 Deploying frontend..." -ForegroundColor Yellow
kubectl apply -f agent-framework/k8s/frontend-deployment.yaml

# Wait for deployments to be ready
Write-Host "⏳ Waiting for deployments to be ready..." -ForegroundColor Yellow
kubectl wait --for=condition=available deployment/agent-backend -n $NAMESPACE --timeout=300s
kubectl wait --for=condition=available deployment/agent-frontend -n $NAMESPACE --timeout=300s

Write-Host "✅ Deployments are ready" -ForegroundColor Green
Write-Host ""

# Deploy Ingress
Write-Host "🌐 Deploying ingress..." -ForegroundColor Yellow
kubectl apply -f agent-framework/k8s/ingress.yaml

# Deploy HPA
Write-Host "📈 Deploying auto-scalers..." -ForegroundColor Yellow
kubectl apply -f agent-framework/k8s/hpa.yaml

Write-Host ""
Write-Host "✅ Deployment completed successfully!" -ForegroundColor Green
Write-Host ""

# Show deployment status
Write-Host "📊 Deployment Status" -ForegroundColor Cyan
Write-Host "===================="
kubectl get pods -n $NAMESPACE
Write-Host ""
kubectl get svc -n $NAMESPACE
Write-Host ""
kubectl get ingress -n $NAMESPACE
Write-Host ""

# Get ingress URL
$ingressInfo = kubectl get ingress agent-ingress -n $NAMESPACE -o json | ConvertFrom-Json
$INGRESS_IP = $ingressInfo.status.loadBalancer.ingress[0].ip
$INGRESS_HOSTNAME = $ingressInfo.status.loadBalancer.ingress[0].hostname

Write-Host "🌍 Access your application at:" -ForegroundColor Cyan
if ($INGRESS_IP) {
    Write-Host "   IP: https://$INGRESS_IP"
}
if ($INGRESS_HOSTNAME) {
    Write-Host "   Hostname: https://$INGRESS_HOSTNAME"
}
Write-Host ""

Write-Host "📝 Next steps:" -ForegroundColor Yellow
Write-Host "1. Point your domain DNS to the ingress IP/hostname"
Write-Host "2. Update configmap.yaml with your domain"
Write-Host "3. Update ingress.yaml with your domain"
Write-Host "4. Update secrets with production values"
Write-Host "5. Configure OAuth redirect URIs in Spotify/Google dashboards"
Write-Host ""
Write-Host "📚 For more information, see: agent-framework/k8s/README.md"
