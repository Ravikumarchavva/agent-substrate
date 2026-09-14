import { NextRequest, NextResponse } from "next/server";
import { requireUserAuthHeaderFromRequest } from "@/lib/engine-auth";

const BACKEND_URL = process.env.BACKEND_API_URL || process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export async function POST(req: NextRequest) {
  const auth = await requireUserAuthHeaderFromRequest(req);
  if (!auth) {
    return NextResponse.json({ error: "Not authenticated" }, { status: 401 });
  }

  const formData = await req.formData();

  const res = await fetch(`${BACKEND_URL}/audio/transcribe`, {
    method: "POST",
    headers: auth.headers,
    body: formData,
  });

  if (!res.ok) {
    const text = await res.text();
    return NextResponse.json({ error: text }, { status: res.status });
  }

  const data = await res.json();
  return NextResponse.json(data);
}
