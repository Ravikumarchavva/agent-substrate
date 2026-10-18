# syntax=docker/dockerfile:1.7
# substrate-data-web: the data workspace (Next.js backend-for-frontend over the OIDC library). Its next.config has no
# standalone output, so the runtime image carries the built app and the production node_modules.
#
# Build (context = monorepo root):
#   docker build -f deploy/docker/substrate-data-web.Dockerfile -t substrate-data-web:local .
# No unauthenticated route exists to probe, so the health check is a TCP connect.
# Runtime configuration: DATA_API_URL, SUBSTRATE_DATA_OIDC_ISSUER, WEB_PUBLIC_URL, WEB_CLIENT_ID, WEB_CLIENT_SECRET, WEB_SESSION_SECRET.
FROM node:22-alpine AS base
RUN corepack enable && corepack prepare pnpm@11.1.1 --activate
WORKDIR /app

FROM base AS build
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY agents/substrate-ui/package.json ./agents/substrate-ui/
COPY agents/substrate-ui/prisma ./agents/substrate-ui/prisma/
COPY data/substrate-data-web/package.json ./data/substrate-data-web/
COPY data/substrate-data-web/scripts ./data/substrate-data-web/scripts/
COPY identity/oidc-bff/package.json ./identity/oidc-bff/
RUN pnpm install --frozen-lockfile --filter ./data/substrate-data-web...
COPY identity/oidc-bff ./identity/oidc-bff
COPY data/substrate-data-web ./data/substrate-data-web
ENV NEXT_TELEMETRY_DISABLED=1
RUN pnpm --filter ./data/substrate-data-web build \
    && pnpm --filter ./data/substrate-data-web --prod deploy --legacy /out
# `pnpm deploy` copies the package with only its production dependencies; the build output is not part of it.
RUN cp -r data/substrate-data-web/.next /out/.next

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
