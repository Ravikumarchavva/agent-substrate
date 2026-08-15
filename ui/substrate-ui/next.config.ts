import type { NextConfig } from "next";

const BACKEND_URL =
  process.env.BACKEND_API_URL || process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const PUBLIC_BACKEND_URL = process.env.NEXT_PUBLIC_API_URL || BACKEND_URL;
// Derive WS URL from the public HTTP backend URL
const WS_BACKEND_URL = PUBLIC_BACKEND_URL.replace(/^http/, "ws");

const nextConfig: NextConfig = {
  output: 'standalone', // For Docker builds
  // Mounted at /chat behind agent-substrate-platform's rewrite proxy, so the
  // whole product lives under one origin (fixes the shared-Google-OAuth-client
  // port collision between the two apps). basePath makes Next.js rewrite all
  // of substrate-ui's own routes, _next/static asset URLs, and Link hrefs to
  // include the /chat prefix automatically — a plain reverse-proxy rewrite
  // without this would silently break asset loading.
  basePath: '/chat',
  // Add your own LAN IP or tunnel hostname (ngrok, etc.) here for local
  // testing from another device — left to just localhost by default so
  // this file doesn't ship anyone's personal dev environment.
  allowedDevOrigins: ['127.0.0.1'],
  experimental: {
    // Enable if needed
  },
  // Expose the WS backend URL to the browser
  env: {
    NEXT_PUBLIC_WS_URL: WS_BACKEND_URL,
  },
  async rewrites() {
    return [
      {
        // WebSocket proxy: browser connects to /api/audio/realtime-ws
        // and Next.js rewrites it to the FastAPI WS endpoint.
        source: "/api/audio/realtime-ws",
        destination: `${BACKEND_URL}/audio/realtime`,
      },
      // /api/backend/* is handled by the catch-all route handler at
      // src/app/api/backend/[...path]/route.ts which adds the engine JWT.
    ];
  },
};

export default nextConfig;
