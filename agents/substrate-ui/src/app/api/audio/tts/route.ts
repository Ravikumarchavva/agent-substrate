import { BACKEND_API } from "@/lib/backend";
import { authed } from "@/lib/session";

export const POST = authed(async (req, auth) => {
  const body = await req.json();

  const res = await fetch(`${BACKEND_API}/audio/tts`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...auth.headers },
    body: JSON.stringify(body),
  });

  if (!res.ok) {
    return Response.json({ error: await res.text() }, { status: res.status });
  }

  // Stream the audio bytes straight to the browser
  return new Response(res.body, {
    status: 200,
    headers: {
      "Content-Type": res.headers.get("Content-Type") || "audio/mpeg",
      "Cache-Control": "no-store",
    },
  });
});
