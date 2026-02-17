import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: 'standalone', // For Docker builds
  experimental: {
    // Enable if needed
  },
};

export default nextConfig;
