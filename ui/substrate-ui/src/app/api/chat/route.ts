import { authHeaderFromCookieHeader } from "@/lib/engine-auth";
import { streamingDispatcher } from "@/lib/streaming-dispatcher";

export async function POST(req: Request) {
  const body = await req.json();

  const BACKEND_URL = process.env.BACKEND_API_URL || process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

  // Per-user identity from the session cookie so the agent (and the files it
  // creates) are scoped to the same user the /api/backend proxy serves files
  // as — otherwise generated files 404 when opened.
  const cookieHeader = req.headers.get("cookie");
  const headers: HeadersInit = {
    "Content-Type": "application/json",
    ...authHeaderFromCookieHeader(cookieHeader),
  };
  if (cookieHeader) (headers as Record<string, string>)["cookie"] = cookieHeader;

  const res = await fetch(`${BACKEND_URL}/chat`, {
    method: "POST",
    headers,
    body: JSON.stringify({
      thread_id: body.thread_id,
      messages: body.messages,
      ...(body.system_instructions ? { system_instructions: body.system_instructions } : {}),
      ...(body.file_ids?.length ? { file_ids: body.file_ids } : {}),
      ...(body.model ? { model: body.model } : {}),
    }),
    // @ts-expect-error — dispatcher is a Node/undici fetch extension, not in the standard fetch() types
    dispatcher: streamingDispatcher,
  });

  if (!res.ok || !res.body) {
    const text = await res.text().catch(() => "Unknown engine error");
    return new Response(
      JSON.stringify({ error: text }),
      { status: res.status, headers: { "Content-Type": "application/json" } }
    );
  }

  return new Response(res.body, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      "Connection": "keep-alive",
    },
  });
}
