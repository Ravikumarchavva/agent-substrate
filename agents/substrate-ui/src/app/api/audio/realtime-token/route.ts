import { BACKEND_API } from "@/lib/backend";
import { authed } from "@/lib/session";

export const GET = authed(async (req, auth) => {
  const search = new URL(req.url).search;
  const res = await fetch(`${BACKEND_API}/audio/realtime-token${search}`, {
    method: "GET",
    headers: { "Content-Type": "application/json", ...auth.headers },
  });

  if (!res.ok) {
    return Response.json({ error: await res.text() }, { status: res.status });
  }

  return Response.json(await res.json());
});
