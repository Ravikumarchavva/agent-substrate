export async function POST(req: Request) {
  const body = await req.json();

  // Forward the request to FastAPI backend with thread_id and messages
  const res = await fetch("http://localhost:8001/chat", {
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
