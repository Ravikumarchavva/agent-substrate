/**
 * GET /api/admin/threads/[threadId]/steps
 * Proxies to backend /admin/threads/{id}/steps (admin only).
 */
import { NextRequest, NextResponse } from "next/server";
import { requireUserAuthHeaderFromRequest } from "@/lib/engine-auth";

const BACKEND_URL = process.env.BACKEND_API_URL || process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ threadId: string }> }
) {
  const auth = await requireUserAuthHeaderFromRequest(req);
  if (!auth || !auth.user.isAdmin) {
    return NextResponse.json({ error: "Forbidden" }, { status: 403 });
  }

  const { threadId } = await params;
  const res = await fetch(`${BACKEND_URL}/admin/threads/${threadId}/steps`, {
    headers: auth.headers,
  });

  const data = await res.json();
  return NextResponse.json(data, { status: res.status });
}
