/**
 * GET /api/admin/threads
 * Proxies to the Agent API's /admin/threads (administrators of the organization only).
 */
import { BACKEND_API } from "@/lib/backend";
import { adminOnly } from "@/lib/session";

export const GET = adminOnly(async (req, auth) => {
  const { searchParams } = new URL(req.url);
  const skip = searchParams.get("skip") ?? "0";
  const limit = searchParams.get("limit") ?? "100";

  const res = await fetch(`${BACKEND_API}/admin/threads?skip=${skip}&limit=${limit}`, { headers: auth.headers });
  return Response.json(await res.json(), { status: res.status });
});
