import { NextRequest, NextResponse } from "next/server";

const BACKEND_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8001";

export async function POST(req: NextRequest) {
  const body = await req.json();

  const res = await fetch(`${BACKEND_URL}/audio/tts`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  if (!res.ok) {
    const text = await res.text();
    return NextResponse.json({ error: text }, { status: res.status });
  }

  // Stream the audio bytes straight to the browser
  return new NextResponse(res.body, {
    status: 200,
    headers: {
      "Content-Type": res.headers.get("Content-Type") || "audio/mpeg",
      "Cache-Control": "no-store",
    },
  });
}
