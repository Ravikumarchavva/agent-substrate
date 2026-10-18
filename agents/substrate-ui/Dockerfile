# Frontend Dockerfile for Next.js
# Node 22 (LTS): pnpm 11.x imports node:sqlite, which requires Node >=22.13.
FROM node:22-alpine AS base

# Install dependencies only when needed
FROM base AS deps
WORKDIR /app

# Install pnpm
# Pin pnpm to the version that generated pnpm-lock.yaml (see packageManager in root package.json) (not @latest, which
# drifts and can require a newer Node than the base image ships).
RUN corepack enable && corepack prepare pnpm@11.1.1 --activate

# Copy workspace manifests (build context is the monorepo root:
#   docker build -f agents/substrate-ui/Dockerfile .)
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY agents/substrate-ui/package.json ./agents/substrate-ui/
COPY agents/substrate-ui/prisma ./agents/substrate-ui/prisma/
COPY identity/oidc-bff/package.json ./identity/oidc-bff/
COPY data/substrate-data-web/package.json ./data/substrate-data-web/
COPY data/substrate-data-web/scripts ./data/substrate-data-web/scripts/

# Install dependencies for the UI only (runs its postinstall: prisma generate)
RUN pnpm install --frozen-lockfile --filter ./agents/substrate-ui...


# Rebuild the source code only when needed
FROM base AS builder
WORKDIR /app

# Install pnpm
# Pin pnpm to the version that generated pnpm-lock.yaml (see packageManager in root package.json) (not @latest, which
# drifts and can require a newer Node than the base image ships).
RUN corepack enable && corepack prepare pnpm@11.1.1 --activate

COPY --from=deps /app/node_modules ./node_modules
COPY --from=deps /app/agents/substrate-ui/node_modules ./agents/substrate-ui/node_modules
COPY package.json pnpm-workspace.yaml ./
COPY identity/oidc-bff ./identity/oidc-bff
COPY agents/substrate-ui ./agents/substrate-ui
WORKDIR /app/agents/substrate-ui


# Build Next.js
ENV NEXT_TELEMETRY_DISABLED=1
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL}
RUN pnpm prisma generate
RUN pnpm build

# Production image, copy all the files and run next
FROM base AS runner
WORKDIR /app

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1

RUN addgroup --system --gid 1001 nodejs
RUN adduser --system --uid 1001 nextjs

# Monorepo standalone output nests the app under agents/substrate-ui/
COPY --from=builder /app/agents/substrate-ui/.next/standalone ./
COPY --from=builder /app/agents/substrate-ui/public ./agents/substrate-ui/public
COPY --from=builder /app/agents/substrate-ui/.next/static ./agents/substrate-ui/.next/static
COPY --from=builder /app/agents/substrate-ui/prisma ./agents/substrate-ui/prisma
COPY --from=builder /app/node_modules ./node_modules

USER nextjs

EXPOSE 3000

ENV PORT=3000
ENV HOSTNAME="0.0.0.0"

CMD ["node", "agents/substrate-ui/server.js"]
