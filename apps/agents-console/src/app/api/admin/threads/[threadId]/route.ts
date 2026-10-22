/**
 * DELETE /api/admin/threads/[threadId]
 * Hard-deletes a thread via the Agent API's admin routes (administrators of the organization only).
 */
import { BACKEND_API } from "@/lib/backend";
import { adminOnly } from "@/lib/session";

export const DELETE = adminOnly<{ params: Promise<{ threadId: string }> }>(async (_req, auth, { params }) => {
  const { threadId } = await params;
  const res = await fetch(`${BACKEND_API}/admin/threads/${encodeURIComponent(threadId)}`, {
    method: "DELETE",
    headers: auth.headers,
  });
  return Response.json(await res.json(), { status: res.status });
});
