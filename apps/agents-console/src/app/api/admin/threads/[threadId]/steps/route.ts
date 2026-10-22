/**
 * GET /api/admin/threads/[threadId]/steps
 * Proxies to the Agent API's /admin/threads/{id}/steps (administrators of the organization only).
 */
import { BACKEND_API } from "@/lib/backend";
import { adminOnly } from "@/lib/session";

export const GET = adminOnly<{ params: Promise<{ threadId: string }> }>(async (_req, auth, { params }) => {
  const { threadId } = await params;
  const res = await fetch(`${BACKEND_API}/admin/threads/${encodeURIComponent(threadId)}/steps`, {
    headers: auth.headers,
  });
  return Response.json(await res.json(), { status: res.status });
});
