import { BACKEND_API } from "@/lib/backend";
import { authed } from "@/lib/session";

export const POST = authed<{ params: Promise<{ requestId: string }> }>(async (req, auth, { params }) => {
  const { requestId } = await params;
  const body = await req.json();

  try {
    const upstream = await fetch(`${BACKEND_API}/chat/respond/${encodeURIComponent(requestId)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...auth.headers },
      body: JSON.stringify(body),
    });

    if (!upstream.ok) {
      return Response.json({ error: await upstream.text() }, { status: upstream.status });
    }

    return Response.json(await upstream.json());
  } catch (err) {
    console.error("HITL respond proxy error:", err);
    return Response.json({ error: "Failed to reach backend" }, { status: 502 });
  }
});
