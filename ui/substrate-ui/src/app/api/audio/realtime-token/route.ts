import { NextRequest, NextResponse } from "next/server";
import { requireUserAuthHeaderFromRequest } from "@/lib/engine-auth";

const BACKEND_URL = process.env.BACKEND_API_URL || process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export async function GET(req: NextRequest) {
  const auth = await requireUserAuthHeaderFromRequest(req);
  if (!auth) {
    return NextResponse.json({ error: "Not authenticated" }, { status: 401 });
  }

  const res = await fetch(`${BACKEND_URL}/audio/realtime-token${req.nextUrl.search}`, {
    method: "GET",
    headers: { "Content-Type": "application/json", ...auth.headers },
  });

  if (!res.ok) {
    const text = await res.text();
    return NextResponse.json({ error: text }, { status: res.status });
  }

  const data = await res.json();
  return NextResponse.json(data);
}
