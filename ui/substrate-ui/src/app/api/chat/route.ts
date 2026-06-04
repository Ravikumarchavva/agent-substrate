import { engineAuthHeader } from "@/lib/engine-auth";

export async function POST(req: Request) {
  const body = await req.json();

  const BACKEND_URL = process.env.BACKEND_API_URL || process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

  const headers: HeadersInit = {
    "Content-Type": "application/json",
    ...engineAuthHeader(),
  };
  const cookieHeader = req.headers.get("cookie");
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
