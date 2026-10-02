-- Enable PostgreSQL extensions used by the framework.
-- This file runs automatically on first DB init (docker-entrypoint-initdb.d).

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
