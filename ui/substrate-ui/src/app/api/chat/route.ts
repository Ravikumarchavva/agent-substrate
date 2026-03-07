export async function POST(req: Request) {
  const body = await req.json();

  // Forward the request to FastAPI backend with thread_id and messages
  const BACKEND_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
  const res = await fetch(`${BACKEND_URL}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      thread_id: body.thread_id,
      messages: body.messages
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
