#!/bin/bash
# Create the glitchtipdb database for the self-hosted GlitchTip error tracker
# (browser-side error tracking for ravi + substrate-ui — see
# docker-compose.yml's glitchtip-* services, profile: glitchtip).
# Docker entrypoint runs all scripts in /docker-entrypoint-initdb.d/ on first
# init only — an already-initialized data volume needs this created manually
# once (docker exec <postgres container> psql -U postgres -c "CREATE DATABASE glitchtipdb;").
set -e

psql -v ON_ERROR_STOP=0 --username "$POSTGRES_USER" <<-EOSQL
  SELECT 'Creating glitchtipdb...' AS status;
  CREATE DATABASE glitchtipdb;
EOSQL
