param(
    [string]$ClusterName = "dev",
    [string]$BackendImageTag = "agent-microservices-kind:local",
    [string]$FrontendImageTag = "chatbot-frontend-kind:local"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$scriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path   # k8s/overlays/kind
$k8sRoot    = Split-Path -Parent (Split-Path -Parent $scriptDir) # k8s/
$repoRoot   = Split-Path -Parent $k8sRoot                       # repo root
$frontendDir = Join-Path (Split-Path -Parent $repoRoot) "ai-chatbot-ui"

Write-Host "=== Microservices Kind Deploy ===" -ForegroundColor Cyan
Write-Host "Cluster       : $ClusterName"
Write-Host "Backend image : $BackendImageTag"
Write-Host "Frontend image: $FrontendImageTag"
Write-Host "Repo root     : $repoRoot"
Write-Host "Frontend dir  : $frontendDir"
Write-Host ""

# ── 0. Verify prerequisites ──────────────────────────────────────────────────
Write-Host "Step 0: Checking prerequisites..." -ForegroundColor Yellow
foreach ($cmd in @("docker", "kind", "kubectl")) {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
        Write-Error "$cmd is required but not found on PATH"
        exit 1
    }
}

$clusters = kind get clusters 2>&1
if ($clusters -notmatch $ClusterName) {
    Write-Error "Kind cluster '$ClusterName' not found. Create it first: kind create cluster --name $ClusterName"
    exit 1
}

kubectl cluster-info --context "kind-$ClusterName" | Out-Null
Write-Host "  kubectl context set to kind-$ClusterName" -ForegroundColor Green

# ── 1. Read secrets from .env ────────────────────────────────────────────────
Write-Host "`nStep 1: Reading secrets from .env..." -ForegroundColor Yellow
$envFile = Join-Path $repoRoot ".env"
if (-not (Test-Path $envFile)) {
    Write-Error "No .env file at $envFile — copy .env.example and fill in values"
    exit 1
}

$envVars = @{}
foreach ($line in Get-Content $envFile) {
    $line = $line.Trim()
    if ($line -and -not $line.StartsWith("#")) {
        $parts = $line -split "=", 2
        if ($parts.Count -eq 2) {
            $envVars[$parts[0].Trim()] = $parts[1].Trim()
        }
    }
}

$OPENAI_API_KEY = $envVars["OPENAI_API_KEY"]
if (-not $OPENAI_API_KEY) {
    Write-Error "OPENAI_API_KEY not found in .env"
    exit 1
}

$JWT_SECRET = [System.Convert]::ToBase64String((1..32 | ForEach-Object { Get-Random -Maximum 256 }) -as [byte[]])
$ENCRYPTION_KEY = & uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" 2>$null
if (-not $ENCRYPTION_KEY) { $ENCRYPTION_KEY = "dev-only-encryption-key-change-me" }

Write-Host "  OPENAI_API_KEY  : $(($OPENAI_API_KEY).Substring(0,8))..." -ForegroundColor Green
Write-Host "  JWT_SECRET      : $(($JWT_SECRET).Substring(0,8))..." -ForegroundColor Green

# ── 2. Build Docker images ───────────────────────────────────────────────────
Write-Host "`nStep 2: Building Docker images..." -ForegroundColor Yellow

Write-Host "  Building backend image: $BackendImageTag"
docker build -t $BackendImageTag -f (Join-Path $repoRoot "Dockerfile") $repoRoot
if ($LASTEXITCODE -ne 0) { Write-Error "Backend Docker build failed"; exit 1 }

if (Test-Path $frontendDir) {
    Write-Host "  Building frontend image: $FrontendImageTag"
    docker build -t $FrontendImageTag `
        --build-arg "NEXT_PUBLIC_API_URL=" `
        -f (Join-Path $frontendDir "Dockerfile") $frontendDir
    if ($LASTEXITCODE -ne 0) { Write-Error "Frontend Docker build failed"; exit 1 }
} else {
    Write-Error "Frontend directory not found at $frontendDir"
    exit 1
}

# ── 3. Load images into Kind ─────────────────────────────────────────────────
Write-Host "`nStep 3: Loading images into Kind cluster..." -ForegroundColor Yellow
kind load docker-image $BackendImageTag --name $ClusterName
kind load docker-image $FrontendImageTag --name $ClusterName
Write-Host "  Images loaded" -ForegroundColor Green

# ── 4. Apply namespaces + infrastructure (Postgres, Redis) ───────────────────
Write-Host "`nStep 4: Deploying namespaces + infrastructure..." -ForegroundColor Yellow
kubectl apply -f (Join-Path $k8sRoot "base\namespaces.yaml")
kubectl apply -f (Join-Path $scriptDir "infra.yaml")
Write-Host "  Waiting for Postgres..." -ForegroundColor Gray
kubectl rollout status statefulset/postgres -n af-data --timeout=180s
Write-Host "  Waiting for Redis..." -ForegroundColor Gray
kubectl rollout status deployment/redis -n af-data --timeout=60s

# ── 5. Create secrets in all namespaces ──────────────────────────────────────
Write-Host "`nStep 5: Creating secrets in all namespaces..." -ForegroundColor Yellow

$DB_URL = "postgresql+asyncpg://postgres:postgres@postgres.af-data.svc.cluster.local:5432/agentdb"
$REDIS_URL = "redis://redis.af-data.svc.cluster.local:6379/0"

$nsSecretMap = @{
    "af-edge"     = "shared-secrets"
    "af-platform" = "platform-secrets"
    "af-runtime"  = "runtime-secrets"
}

foreach ($entry in $nsSecretMap.GetEnumerator()) {
    $ns = $entry.Key
    $secretName = $entry.Value
    kubectl create secret generic $secretName `
        --namespace=$ns `
        --from-literal="DATABASE_URL=$DB_URL" `
        --from-literal="REDIS_URL=$REDIS_URL" `
        --from-literal="JWT_SECRET=$JWT_SECRET" `
        --from-literal="OPENAI_API_KEY=$OPENAI_API_KEY" `
        --from-literal="ENCRYPTION_KEY=$ENCRYPTION_KEY" `
        --dry-run=client -o yaml | kubectl apply -f -
    Write-Host "  $secretName in $ns" -ForegroundColor Green
}

# Frontend OAuth secrets (optional — read from .env if present)
$googleClientId = $envVars["GOOGLE_CLIENT_ID"]
$googleClientSecret = $envVars["GOOGLE_CLIENT_SECRET"]
$spotifyClientId = $envVars["SPOTIFY_CLIENT_ID"]
$spotifyClientSecret = $envVars["SPOTIFY_CLIENT_SECRET"]

if ($googleClientId -or $spotifyClientId) {
    kubectl create secret generic frontend-oauth-secrets `
        --namespace=af-edge `
        --from-literal="GOOGLE_CLIENT_ID=$googleClientId" `
        --from-literal="GOOGLE_CLIENT_SECRET=$googleClientSecret" `
        --from-literal="SPOTIFY_CLIENT_ID=$spotifyClientId" `
        --from-literal="SPOTIFY_CLIENT_SECRET=$spotifyClientSecret" `
        --dry-run=client -o yaml | kubectl apply -f -
    Write-Host "  frontend-oauth-secrets in af-edge" -ForegroundColor Green
}

# ── 6. Deploy everything via kustomize ───────────────────────────────────────
Write-Host "`nStep 6: Deploying all services via kustomize..." -ForegroundColor Yellow
kubectl apply -k $scriptDir
Write-Host "  Kustomize apply complete" -ForegroundColor Green

# ── 7. Force rollout restart (to catch same-tag image updates) ──────────────
Write-Host "`nStep 7: Restarting deployments for fresh images..." -ForegroundColor Yellow

$deployments = @(
    @{Name="gateway-bff";          NS="af-edge"},
    @{Name="frontend";             NS="af-edge"},
    @{Name="identity-auth";        NS="af-platform"},
    @{Name="policy-authorization"; NS="af-platform"},
    @{Name="conversation";         NS="af-runtime"},
    @{Name="job-controller";       NS="af-runtime"},
    @{Name="agent-runtime";        NS="af-runtime"},
    @{Name="tool-executor";        NS="af-runtime"},
    @{Name="human-gate";           NS="af-runtime"},
    @{Name="live-stream";          NS="af-runtime"},
    @{Name="file-store";           NS="af-runtime"},
    @{Name="admin";                NS="af-runtime"}
)

foreach ($d in $deployments) {
    kubectl rollout restart deployment/$($d.Name) -n $($d.NS) 2>$null
}

# ── 8. Wait for all rollouts ─────────────────────────────────────────────────
Write-Host "`nStep 8: Waiting for all deployments to be ready..." -ForegroundColor Yellow

$failed = @()
foreach ($d in $deployments) {
    Write-Host "  Waiting: $($d.Name) in $($d.NS)..." -ForegroundColor Gray -NoNewline
    kubectl rollout status deployment/$($d.Name) -n $($d.NS) --timeout=180s 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Host " FAILED" -ForegroundColor Red
        $failed += "$($d.NS)/$($d.Name)"
    } else {
        Write-Host " OK" -ForegroundColor Green
    }
}

if ($failed.Count -gt 0) {
    Write-Host "`n=== WARNING: These deployments failed to become ready ===" -ForegroundColor Red
    $failed | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
    Write-Host "Check logs: kubectl logs -n <namespace> deployment/<name>`n"
}

# ── 9. Summary ───────────────────────────────────────────────────────────────
Write-Host "`n=== Deployment Summary ===" -ForegroundColor Cyan
Write-Host "Namespaces:"
kubectl get ns -l app.kubernetes.io/part-of=agent-framework -o custom-columns=NAME:.metadata.name --no-headers 2>$null | ForEach-Object { Write-Host "  $_" }

Write-Host "`nAll pods:"
foreach ($ns in @("af-data", "af-edge", "af-platform", "af-runtime", "af-observability")) {
    $pods = kubectl get pods -n $ns --no-headers 2>$null
    if ($pods) {
        Write-Host "  [$ns]" -ForegroundColor Yellow
        $pods -split "`n" | ForEach-Object { Write-Host "    $_" }
    }
}

Write-Host "`n=== Access ===" -ForegroundColor Cyan
Write-Host "  Frontend     : http://localhost/"
Write-Host "  Gateway API  : http://localhost/chat, /threads, /stream, ..."
Write-Host "  Grafana      : http://localhost/grafana/"
Write-Host "  Health check : http://localhost/health"
Write-Host ""
Write-Host "=== Done ===" -ForegroundColor Green
