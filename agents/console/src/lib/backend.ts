/**
 * Where the Agent API lives. Every call from this app's server side goes to the versioned API (`/api/v1`).
 * `BACKEND_API_URL` is the origin only (no path).
 */
export const BACKEND_ORIGIN =
  process.env.BACKEND_API_URL || process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";

export const BACKEND_API = `${BACKEND_ORIGIN.replace(/\/$/, "")}/api/v1`;
