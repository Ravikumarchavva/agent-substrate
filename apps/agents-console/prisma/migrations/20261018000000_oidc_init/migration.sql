-- Accounts and sessions live in Keycloak; only connector credentials (keyed by the Keycloak user id) remain here.
CREATE TABLE "user_credentials" (
    "id" TEXT NOT NULL,
    "user_id" TEXT NOT NULL,
    "provider" TEXT NOT NULL,
    "access_token" TEXT NOT NULL,
    "refresh_token" TEXT,
    "token_type" TEXT,
    "expires_at" TIMESTAMP(3),
    "scope" TEXT,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "user_credentials_pkey" PRIMARY KEY ("id")
);

CREATE INDEX "user_credentials_user_id_idx" ON "user_credentials"("user_id");

CREATE UNIQUE INDEX "user_credentials_user_id_provider_key" ON "user_credentials"("user_id", "provider");
