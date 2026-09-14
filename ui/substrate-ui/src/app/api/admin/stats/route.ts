/**
 * GET /api/admin/stats
 * Proxies to backend /admin/stats (admin only).
 */
import { NextRequest, NextResponse } from "next/server";
import { requireUserAuthHeaderFromRequest } from "@/lib/engine-auth";

const BACKEND_URL = process.env.BACKEND_API_URL || process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export async function GET(req: NextRequest) {
  const auth = await requireUserAuthHeaderFromRequest(req);
  if (!auth || !auth.user.isAdmin) {
    return NextResponse.json({ error: "Forbidden" }, { status: 403 });
  }

  // A real admin JWT, not the X-Admin-Email header the backend never read
  // (require_admin needs role=tenant_admin/platform_admin in the token).
  const res = await fetch(`${BACKEND_URL}/admin/stats`, {
    headers: auth.headers,
  });

  const data = await res.json();
  return NextResponse.json(data, { status: res.status });
}
