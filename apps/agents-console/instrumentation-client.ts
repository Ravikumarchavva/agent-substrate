/**
 * Browser-only error tracking, pointed at a self-hosted GlitchTip instance
 * (MIT-licensed, Sentry-protocol-compatible — see deployment/docker/
 * docker-compose.yml's glitchtip-* services in agent-substrate).
 *
 * Same rationale as ravi's src/instrumentation-client.ts: no
 * withSentryConfig wrapper (that pushes source maps to Sentry's own cloud
 * API, not this self-hosted instance), client-only (server-side errors
 * already flow into the existing Promtail→Loki path via structured stdout
 * logging).
 *
 * Inert until NEXT_PUBLIC_GLITCHTIP_DSN is set — Sentry.init with an empty
 * dsn is a documented no-op, not an error.
 */
import * as Sentry from "@sentry/nextjs";

Sentry.init({
  dsn: process.env.NEXT_PUBLIC_GLITCHTIP_DSN,
  tracesSampleRate: 0, // errors only — no performance/tracing overhead
});
