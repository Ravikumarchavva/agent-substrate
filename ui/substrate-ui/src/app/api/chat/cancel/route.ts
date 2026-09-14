/**
 * POST /api/chat/cancel
 * Proxies a cancellation request to the FastAPI backend so the running
 * agent stream for a given thread is stopped.
 */
import { requireUserAuthHeader } from "@/lib/engine-auth";

export async function POST(req: Request) {
  const { thread_id } = (await req.json()) as { thread_id: string };

  const auth = await requireUserAuthHeader(req.headers.get("cookie"));
  if (!auth) {
    return Response.json({ error: "Not authenticated" }, { status: 401 });
  }

  const BACKEND_URL =
    process.env.BACKEND_API_URL || process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

  const res = await fetch(`${BACKEND_URL}/chat/${thread_id}/cancel`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...auth.headers,
    },
  });

  const data = await res.json();
  return Response.json(data, { status: res.status });
}
