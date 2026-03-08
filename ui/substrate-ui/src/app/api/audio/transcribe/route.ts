import { NextRequest, NextResponse } from "next/server";

const BACKEND_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8001";

export async function POST(req: NextRequest) {
  const formData = await req.formData();

  const res = await fetch(`${BACKEND_URL}/audio/transcribe`, {
    method: "POST",
    body: formData,
  });

  if (!res.ok) {
    const text = await res.text();
    return NextResponse.json({ error: text }, { status: res.status });
  }

  const data = await res.json();
  return NextResponse.json(data);
}
