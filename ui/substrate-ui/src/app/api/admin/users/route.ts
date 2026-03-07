/**
 * GET /api/admin/users
 * Returns all registered users from Prisma (admin only).
 */
import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";

const ADMIN_EMAIL = "chavvaravikumarreddy2004@gmail.com";

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

  const users = await prisma.user.findMany({
    orderBy: { createdAt: "desc" },
    select: {
      id: true,
      email: true,
      name: true,
      avatarUrl: true,
      isAdmin: true,
      createdAt: true,
    },
  });

  return NextResponse.json(users);
}
