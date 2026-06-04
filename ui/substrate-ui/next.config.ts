import type { NextConfig } from "next";

const BACKEND_URL =
  process.env.BACKEND_API_URL || process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const PUBLIC_BACKEND_URL = process.env.NEXT_PUBLIC_API_URL || BACKEND_URL;
// Derive WS URL from the public HTTP backend URL
const WS_BACKEND_URL = PUBLIC_BACKEND_URL.replace(/^http/, "ws");

const nextConfig: NextConfig = {
  output: 'standalone', // For Docker builds
  allowedDevOrigins: ['127.0.0.1', '192.168.0.6', 'coagulant-stump-starlet.ngrok-free.dev'],
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
