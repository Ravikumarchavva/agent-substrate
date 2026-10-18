import { BACKEND_API } from "@/lib/backend";
import { authed } from "@/lib/session";

export const POST = authed(async (req, auth) => {
  const formData = await req.formData();

  const res = await fetch(`${BACKEND_API}/audio/transcribe`, {
    method: "POST",
    headers: auth.headers,
    body: formData,
  });

  if (!res.ok) {
    return Response.json({ error: await res.text() }, { status: res.status });
  }

  return Response.json(await res.json());
});
