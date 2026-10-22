# syntax=docker/dockerfile:1.7
# loamz-data-workspace: the data workspace (Next.js backend-for-frontend over the OIDC library).
FROM node:22-alpine AS base
RUN corepack enable && corepack prepare pnpm@11.1.1 --activate
WORKDIR /app

FROM base AS build
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/agents-console/package.json ./apps/agents-console/
COPY apps/agents-console/prisma ./apps/agents-console/prisma/
COPY apps/data-workspace/package.json ./apps/data-workspace/
COPY apps/data-workspace/scripts ./apps/data-workspace/scripts/
COPY sdk/js/oidc-bff/package.json ./sdk/js/oidc-bff/
RUN pnpm install --frozen-lockfile --filter ./apps/data-workspace...
COPY sdk/js/oidc-bff ./sdk/js/oidc-bff
COPY apps/data-workspace ./apps/data-workspace
ENV NEXT_TELEMETRY_DISABLED=1
RUN pnpm --filter ./apps/data-workspace build \
    && pnpm --filter ./apps/data-workspace --prod deploy --legacy /out
RUN cp -r apps/data-workspace/.next /out/.next

FROM node:22-alpine AS runtime
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 HOSTNAME=0.0.0.0 PORT=3011
RUN addgroup --system --gid 1001 nodejs && adduser --system --uid 1001 nextjs
COPY --from=build --chown=nextjs:nodejs /out ./
USER nextjs
EXPOSE 3011
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD nc -z 127.0.0.1 3011 || exit 1
CMD ["node_modules/.bin/next", "start", "--hostname", "0.0.0.0", "--port", "3011"]

