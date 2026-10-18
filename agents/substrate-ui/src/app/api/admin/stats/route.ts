/**
 * GET /api/admin/stats
 * Proxies to the Agent API's /admin/stats (administrators of the organization only).
 */
import { BACKEND_API } from "@/lib/backend";
import { adminOnly } from "@/lib/session";

export const GET = adminOnly(async (_req, auth) => {
  const res = await fetch(`${BACKEND_API}/admin/stats`, { headers: auth.headers });
  return Response.json(await res.json(), { status: res.status });
});
