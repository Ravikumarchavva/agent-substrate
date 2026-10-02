#!/bin/bash
# Create the chatbotdb database for ravi-ui (Prisma).
# Docker entrypoint runs all scripts in /docker-entrypoint-initdb.d/ on first init.
set -e

psql -v ON_ERROR_STOP=0 --username "$POSTGRES_USER" <<-EOSQL
  SELECT 'Creating chatbotdb...' AS status;
  CREATE DATABASE chatbotdb;
EOSQL
