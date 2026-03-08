import type { NextConfig } from "next";

const BACKEND_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8001";
// Derive WS URL from HTTP backend URL
const WS_BACKEND_URL = BACKEND_URL.replace(/^http/, "ws");

const nextConfig: NextConfig = {
  output: 'standalone', // For Docker builds
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
    ];
  },
};

export default nextConfig;
