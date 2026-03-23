/**
 * GET /api/admin/stats
 * Proxies to backend /admin/stats (admin only).
 */
import { NextRequest, NextResponse } from "next/server";

const ADMIN_EMAIL = "chavvaravikumarreddy2004@gmail.com";
const BACKEND_URL = process.env.BACKEND_API_URL || process.env.NEXT_PUBLIC_API_URL || "http://localhost:8001";

function getAdminEmail(req: NextRequest): string | null {
  const cookie = req.cookies.get("google_user")?.value;
  if (!cookie) return null;
  try {
    const user = JSON.parse(decodeURIComponent(cookie));
    return user?.email ?? null;
  } catch {
    return null;
  }
}

export async function GET(req: NextRequest) {
  const email = getAdminEmail(req);
  if (!email || email.toLowerCase() !== ADMIN_EMAIL.toLowerCase()) {
    return NextResponse.json({ error: "Forbidden" }, { status: 403 });
  }

  const res = await fetch(`${BACKEND_URL}/admin/stats`, {
    headers: { "X-Admin-Email": email },
  });

  const data = await res.json();
  return NextResponse.json(data, { status: res.status });
}
