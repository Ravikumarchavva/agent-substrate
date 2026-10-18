#!/bin/sh
# Write a .env with freshly generated secrets next to compose.yml. Refuses to overwrite an existing one.
set -eu
cd "$(dirname "$0")"
[ -e .env ] && { echo ".env exists; delete it first if you want new secrets" >&2; exit 1; }
rand() { openssl rand -base64 "$1" | tr -d '=+/\n' | cut -c1-"$2"; }
umask 077
sed \
  -e "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$(rand 24 24)|" \
  -e "s|^REDIS_PASSWORD=.*|REDIS_PASSWORD=$(rand 24 24)|" \
  -e "s|^KEYCLOAK_ADMIN_PASSWORD=.*|KEYCLOAK_ADMIN_PASSWORD=$(rand 24 24)|" \
  -e "s|^SUBSTRATE_UI_CLIENT_SECRET=.*|SUBSTRATE_UI_CLIENT_SECRET=$(rand 36 36)|" \
  -e "s|^SUBSTRATE_DATA_WEB_CLIENT_SECRET=.*|SUBSTRATE_DATA_WEB_CLIENT_SECRET=$(rand 36 36)|" \
  -e "s|^CELL_ADMIN_CLIENT_SECRET=.*|CELL_ADMIN_CLIENT_SECRET=$(rand 36 36)|" \
  -e "s|^CONTROL_S3_ACCESS_KEY_ID=.*|CONTROL_S3_ACCESS_KEY_ID=SDC$(openssl rand -hex 10 | tr a-f A-F)|" \
  -e "s|^CONTROL_S3_SECRET_ACCESS_KEY=.*|CONTROL_S3_SECRET_ACCESS_KEY=$(rand 36 36)|" \
  -e "s|^WORKER_S3_ACCESS_KEY_ID=.*|WORKER_S3_ACCESS_KEY_ID=SDW$(openssl rand -hex 10 | tr a-f A-F)|" \
  -e "s|^WORKER_S3_SECRET_ACCESS_KEY=.*|WORKER_S3_SECRET_ACCESS_KEY=$(rand 36 36)|" \
  -e "s|^AGENTS_S3_ACCESS_KEY_ID=.*|AGENTS_S3_ACCESS_KEY_ID=SDA$(openssl rand -hex 10 | tr a-f A-F)|" \
  -e "s|^AGENTS_S3_SECRET_ACCESS_KEY=.*|AGENTS_S3_SECRET_ACCESS_KEY=$(rand 36 36)|" \
  -e "s|^CONTROLLER_TOKEN=.*|CONTROLLER_TOKEN=$(rand 48 48)|" \
  -e "s|^WORKER_TOKEN=.*|WORKER_TOKEN=$(rand 48 48)|" \
  -e "s|^WEB_SESSION_SECRET=.*|WEB_SESSION_SECRET=$(rand 48 48)|" \
  -e "s|^UI_SESSION_SECRET=.*|UI_SESSION_SECRET=$(rand 48 48)|" \
  -e "s|^MASK_SALT=.*|MASK_SALT=$(rand 24 24)|" \
  .env.example > .env
echo "wrote $(pwd)/.env (mode 600). Add an LLM key, then: docker compose --profile monolith up -d"
