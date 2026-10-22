import type { NextConfig } from "next";

const config: NextConfig = {
  poweredByHeader: false,
  // The shared BFF library ships TypeScript source.
  transpilePackages: ["@loamz/oidc-bff"],
  distDir: process.env.LOAMZ_WORKSPACE_DIST_DIR || ".next",
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Frame-Options", value: "DENY" },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "same-origin" },
        ],
      },
    ];
  },
};
export default config;
