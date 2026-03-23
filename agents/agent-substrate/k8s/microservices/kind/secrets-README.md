# Kind-specific secrets — values injected by deploy-microservices-kind.ps1
# This file is a TEMPLATE; the deploy script generates real secrets via
# kubectl create secret ... --dry-run=client -o yaml | kubectl apply -f -
#
# The script creates these secrets in all 3 service namespaces:
#   af-edge:    shared-secrets
#   af-platform: platform-secrets
#   af-runtime:  runtime-secrets
#
# Each contains:
#   DATABASE_URL, REDIS_URL, JWT_SECRET, OPENAI_API_KEY, ENCRYPTION_KEY
