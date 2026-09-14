/**
 * GET /api/admin/threads
 * Proxies to backend /admin/threads (admin only).
 */
import { NextRequest, NextResponse } from "next/server";
import { requireUserAuthHeaderFromRequest } from "@/lib/engine-auth";

const BACKEND_URL = process.env.BACKEND_API_URL || process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export async function GET(req: NextRequest) {
  const auth = await requireUserAuthHeaderFromRequest(req);
  if (!auth || !auth.user.isAdmin) {
    return NextResponse.json({ error: "Forbidden" }, { status: 403 });
  }

  const { searchParams } = new URL(req.url);
  const skip = searchParams.get("skip") ?? "0";
  const limit = searchParams.get("limit") ?? "100";

  const res = await fetch(
    `${BACKEND_URL}/admin/threads?skip=${skip}&limit=${limit}`,
    { headers: auth.headers }
  );

  const data = await res.json();
  return NextResponse.json(data, { status: res.status });
}
