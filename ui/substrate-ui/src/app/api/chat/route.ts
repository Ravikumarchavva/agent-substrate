export async function POST(req: Request) {
  const body = await req.json();

  // Forward the request to FastAPI backend with thread_id, messages and optional system_instructions
  // BACKEND_API_URL is for server-side (internal cluster); NEXT_PUBLIC_API_URL is build-time fallback
  const BACKEND_URL = process.env.BACKEND_API_URL || process.env.NEXT_PUBLIC_API_URL || "http://localhost:8001";
  const res = await fetch(`${BACKEND_URL}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      thread_id: body.thread_id,
      messages: body.messages,
      ...(body.system_instructions ? { system_instructions: body.system_instructions } : {}),
      ...(body.file_ids?.length ? { file_ids: body.file_ids } : {}),
    }),
  });

  if (!res.body) {
    return new Response("No stream", { status: 500 });
  }

  // Forward the SSE stream directly
  return new Response(res.body, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      "Connection": "keep-alive",
    },
  });
}
