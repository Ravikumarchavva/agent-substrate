# Frontend Dockerfile for Next.js
# Node 22 (LTS): pnpm 11.x imports node:sqlite, which requires Node >=22.13.
FROM node:22-alpine AS base

# Install dependencies only when needed
FROM base AS deps
WORKDIR /app

# Install pnpm
# Pin pnpm to the version that generated pnpm-lock.yaml (not @latest, which
# drifts and can require a newer Node than the base image ships).
RUN corepack enable && corepack prepare pnpm@11.5.2 --activate

# Copy package files
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY prisma ./prisma/

# Install dependencies
RUN pnpm install --frozen-lockfile

# Generate Prisma client
RUN pnpm prisma generate


# Rebuild the source code only when needed
FROM base AS builder
WORKDIR /app

# Install pnpm
# Pin pnpm to the version that generated pnpm-lock.yaml (not @latest, which
# drifts and can require a newer Node than the base image ships).
RUN corepack enable && corepack prepare pnpm@11.5.2 --activate

COPY --from=deps /app/node_modules ./node_modules
COPY . .


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

COPY --from=builder /app/public ./public
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/prisma ./prisma
COPY --from=builder /app/node_modules ./node_modules

USER nextjs

EXPOSE 3000

ENV PORT=3000
ENV HOSTNAME="0.0.0.0"

CMD ["node", "server.js"]
