import { requireUserAuthHeader } from "@/lib/engine-auth";
import { streamingDispatcher } from "@/lib/streaming-dispatcher";

export async function POST(req: Request) {
  const body = await req.json();

  const BACKEND_URL = process.env.BACKEND_API_URL || process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

  // Per-user identity from the DB-backed session (src/lib/session.ts) so the
  // agent (and the files it creates) are scoped to the same user the
  // /api/backend proxy serves files as — otherwise generated files 404 when
  // opened. No session → 401, not a silent anonymous/service fallback.
  const auth = await requireUserAuthHeader(req.headers.get("cookie"));
  if (!auth) {
    return new Response(JSON.stringify({ error: "Not authenticated" }), {
      status: 401,
      headers: { "Content-Type": "application/json" },
    });
  }

  const headers: HeadersInit = {
    "Content-Type": "application/json",
    ...auth.headers,
  };

  const res = await fetch(`${BACKEND_URL}/chat`, {
    method: "POST",
    headers,
    body: JSON.stringify({
      thread_id: body.thread_id,
      messages: body.messages,
      ...(body.system_instructions ? { system_instructions: body.system_instructions } : {}),
      ...(body.file_ids?.length ? { file_ids: body.file_ids } : {}),
      ...(body.model ? { model: body.model } : {}),
      ...(body.branch_id ? { branch_id: body.branch_id } : {}),
    }),
    // @ts-expect-error — dispatcher is a Node/undici fetch extension, not in the standard fetch() types
    dispatcher: streamingDispatcher,
  });

  if (!res.ok || !res.body) {
    const text = await res.text().catch(() => "Unknown engine error");
    // The backend's error body is itself JSON (FastAPI's {"detail": "..."}
    // for e.g. a 423 locked-conversation response) — wrapping that raw text
    // as a plain string inside {"error": text} double-encoded it, so the
    // banner showed the literal JSON text instead of the human-readable
    // message. Forward the backend's own shape when it parses as JSON;
    // only fall back to wrapping raw text for a genuinely non-JSON body.
    let body: string;
    try {
      JSON.parse(text);
      body = text;
    } catch {
      body = JSON.stringify({ error: text });
    }
    return new Response(body, {
      status: res.status,
      headers: { "Content-Type": "application/json" },
    });
  }

  return new Response(res.body, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      "Connection": "keep-alive",
    },
  });
}
