/**
 * POST /api/chat/cancel
 * Proxies a cancellation request to the Agent API so the running
 * agent stream for a given thread is stopped.
 */
import { BACKEND_API } from "@/lib/backend";
import { authed } from "@/lib/session";

export const POST = authed(async (req, auth) => {
  const { thread_id } = (await req.json()) as { thread_id: string };

  const res = await fetch(`${BACKEND_API}/chat/${encodeURIComponent(thread_id)}/cancel`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...auth.headers,
    },
  });

  return Response.json(await res.json(), { status: res.status });
});
