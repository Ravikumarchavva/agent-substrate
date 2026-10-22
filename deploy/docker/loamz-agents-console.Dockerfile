# syntax=docker/dockerfile:1.7
# loamz-agents-console: Next.js standalone output, non-root.
# Node 22 (LTS): pnpm 11.x imports node:sqlite, which requires Node >=22.13.
FROM node:22-alpine AS base

FROM base AS deps
WORKDIR /app

RUN corepack enable && corepack prepare pnpm@11.1.1 --activate

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/agents-console/package.json ./apps/agents-console/
COPY apps/agents-console/prisma ./apps/agents-console/prisma/
COPY sdk/js/oidc-bff/package.json ./sdk/js/oidc-bff/
COPY apps/data-workspace/package.json ./apps/data-workspace/
COPY apps/data-workspace/scripts ./apps/data-workspace/scripts/

RUN pnpm install --frozen-lockfile --filter ./apps/agents-console...

FROM base AS builder
WORKDIR /app

RUN corepack enable && corepack prepare pnpm@11.1.1 --activate

COPY --from=deps /app/node_modules ./node_modules
COPY --from=deps /app/apps/agents-console/node_modules ./apps/agents-console/node_modules
COPY package.json pnpm-workspace.yaml ./
COPY sdk/js/oidc-bff ./sdk/js/oidc-bff
COPY apps/agents-console ./apps/agents-console
WORKDIR /app/apps/agents-console

ENV NEXT_TELEMETRY_DISABLED=1
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL}
RUN pnpm prisma generate
RUN pnpm build

FROM base AS runner
WORKDIR /app

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1

RUN addgroup --system --gid 1001 nodejs
RUN adduser --system --uid 1001 nextjs

COPY --from=builder /app/apps/agents-console/.next/standalone ./
COPY --from=builder /app/apps/agents-console/public ./apps/agents-console/public
COPY --from=builder /app/apps/agents-console/.next/static ./apps/agents-console/.next/static
COPY --from=builder /app/apps/agents-console/prisma ./apps/agents-console/prisma
COPY --from=builder /app/node_modules ./node_modules

USER nextjs

EXPOSE 3000

ENV PORT=3000
ENV HOSTNAME="0.0.0.0"

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1:3000/chat || exit 1

CMD ["node", "apps/agents-console/server.js"]

