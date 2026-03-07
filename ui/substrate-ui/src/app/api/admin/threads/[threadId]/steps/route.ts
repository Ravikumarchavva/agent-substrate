/**
 * GET /api/admin/threads/[threadId]/steps
 * Proxies to backend /admin/threads/{id}/steps.
 */
import { NextRequest, NextResponse } from "next/server";

const ADMIN_EMAIL = "chavvaravikumarreddy2004@gmail.com";
const BACKEND_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

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

export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ threadId: string }> }
) {
  const email = getAdminEmail(req);
  if (!email || email.toLowerCase() !== ADMIN_EMAIL.toLowerCase()) {
    return NextResponse.json({ error: "Forbidden" }, { status: 403 });
  }

  const { threadId } = await params;
  const res = await fetch(`${BACKEND_URL}/admin/threads/${threadId}/steps`, {
    headers: { "X-Admin-Email": email },
  });

  const data = await res.json();
  return NextResponse.json(data, { status: res.status });
}
