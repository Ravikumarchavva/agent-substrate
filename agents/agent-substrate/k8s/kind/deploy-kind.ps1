param(
    [string]$ClusterName = "dev"
)

$ErrorActionPreference = "Stop"

function Get-DotEnvValue {
    param(
        [string]$Path,
        [string]$Key,
        [string]$Default = ""
    )

    if (-not (Test-Path $Path)) {
        return $Default
    }

    $match = Select-String -Path $Path -Pattern "^$Key=(.*)$" | Select-Object -First 1
    if (-not $match) {
        return $Default
    }

    $value = $match.Matches[0].Groups[1].Value.Trim()
    if ($value.StartsWith('"') -and $value.EndsWith('"')) {
        return $value.Trim('"')
    }
    return $value
}

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $repoRoot

if (-not (Get-Command kind -ErrorAction SilentlyContinue)) {
    throw "kind is not installed"
}
if (-not (Get-Command kubectl -ErrorAction SilentlyContinue)) {
    throw "kubectl is not installed"
}

$clusters = kind get clusters
if ($clusters -notcontains $ClusterName) {
    throw "Kind cluster '$ClusterName' does not exist. Create it first or pass -ClusterName."
}

$dotenvPath = Join-Path $repoRoot ".env"
$openAiKey = Get-DotEnvValue -Path $dotenvPath -Key "OPENAI_API_KEY"
if (-not $openAiKey) {
    throw "OPENAI_API_KEY was not found in .env"
}

$spotifyClientId = Get-DotEnvValue -Path $dotenvPath -Key "SPOTIFY_CLIENT_ID"
$spotifyClientSecret = Get-DotEnvValue -Path $dotenvPath -Key "SPOTIFY_CLIENT_SECRET"
$anthropicKey = Get-DotEnvValue -Path $dotenvPath -Key "ANTHROPIC_API_KEY"

$encryptionKey = uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
if ($LASTEXITCODE -ne 0 -or -not $encryptionKey) {
    throw "Failed to generate ENCRYPTION_KEY"
}

Write-Host "Building backend image for Kind..."
docker build -t agent-backend-kind:local -f Dockerfile .
if ($LASTEXITCODE -ne 0) {
    throw "Docker build failed"
}

Write-Host "Loading backend image into Kind cluster '$ClusterName'..."
kind load docker-image agent-backend-kind:local --name $ClusterName
if ($LASTEXITCODE -ne 0) {
    throw "kind load docker-image failed"
}

kubectl apply -f k8s/namespace.yaml

$secretArgs = @(
    "create", "secret", "generic", "agent-secrets",
    "-n", "agent-framework",
    "--from-literal=DB_PASSWORD=postgres",
    "--from-literal=DATABASE_URL=postgresql+asyncpg://agent:postgres@postgres.agent-framework.svc.cluster.local:5432/agent_framework",
    "--from-literal=REDIS_URL=redis://redis.agent-framework.svc.cluster.local:6379/0",
    "--from-literal=ENCRYPTION_KEY=$encryptionKey",
    "--from-literal=OPENAI_API_KEY=$openAiKey",
    "--from-literal=ANTHROPIC_API_KEY=$anthropicKey",
    "--from-literal=SPOTIFY_CLIENT_ID=$spotifyClientId",
    "--from-literal=SPOTIFY_CLIENT_SECRET=$spotifyClientSecret",
    "--from-literal=NEXTAUTH_SECRET=kind-dev-nextauth-secret",
    "--dry-run=client",
    "-o", "yaml"
)

& kubectl @secretArgs | kubectl apply -f -
if ($LASTEXITCODE -ne 0) {
    throw "Failed to apply agent-secrets"
}

Write-Host "Applying Kind overlay..."
kubectl kustomize k8s/kind --load-restrictor=LoadRestrictionsNone | kubectl apply -f -
if ($LASTEXITCODE -ne 0) {
    throw "kubectl apply -k failed"
}

Write-Host "Restarting backend deployment to pick up the freshly loaded local image..."
kubectl rollout restart deployment/agent-backend -n agent-framework
if ($LASTEXITCODE -ne 0) {
    throw "Failed to restart backend deployment"
}

Write-Host "Waiting for PostgreSQL..."
kubectl rollout status statefulset/postgres -n agent-framework --timeout=300s
if ($LASTEXITCODE -ne 0) {
    throw "PostgreSQL rollout failed"
}
Write-Host "Waiting for Redis..."
kubectl rollout status deployment/redis -n agent-framework --timeout=180s
if ($LASTEXITCODE -ne 0) {
    throw "Redis rollout failed"
}
Write-Host "Waiting for backend..."
kubectl rollout status deployment/agent-backend -n agent-framework --timeout=300s
if ($LASTEXITCODE -ne 0) {
    throw "Backend rollout failed"
}

Write-Host "Current workloads:"
kubectl get pods,svc -n agent-framework

Write-Host "Kind deployment completed."
Write-Host "Port-forward backend with: kubectl port-forward -n agent-framework svc/agent-backend 8000:8000"