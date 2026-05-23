import { NextRequest, NextResponse } from "next/server";

export async function POST(
  req: NextRequest,
  { params }: { params: Promise<{ requestId: string }> }
) {
  const { requestId } = await params;
  const body = await req.json();

  try {
    const BACKEND_URL = process.env.BACKEND_API_URL || process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
    const headers: HeadersInit = { "Content-Type": "application/json" };
    const authHeader = req.headers.get("authorization");
    if (authHeader) headers["Authorization"] = authHeader;
    const cookieHeader = req.headers.get("cookie");
    if (cookieHeader) headers["cookie"] = cookieHeader;

    const upstream = await fetch(
      `${BACKEND_URL}/chat/respond/${requestId}`,
      {
        method: "POST",
        headers,
        body: JSON.stringify(body),
      }
    );

    if (!upstream.ok) {
      const text = await upstream.text();
      return NextResponse.json(
        { error: text },
        { status: upstream.status }
      );
    }

    const data = await upstream.json();
    return NextResponse.json(data);
  } catch (err) {
    console.error("HITL respond proxy error:", err);
    return NextResponse.json(
      { error: "Failed to reach backend" },
      { status: 502 }
    );
  }
}
