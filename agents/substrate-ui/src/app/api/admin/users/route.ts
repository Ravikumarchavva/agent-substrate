/**
 * GET /api/admin/users
 * Returns all registered users from Prisma (admin only).
 */
import { Prisma } from "@prisma/client";
import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";
import { getSessionFromRequest } from "@/lib/session";

function isDatabaseUnavailableError(error: unknown): boolean {
  return (
    error instanceof Prisma.PrismaClientInitializationError ||
    (error instanceof Prisma.PrismaClientKnownRequestError && error.code === "P1001")
  );
}

export async function GET(req: NextRequest) {
  const session = await getSessionFromRequest(req);
  if (!session || !session.isAdmin) {
    return NextResponse.json({ error: "Forbidden" }, { status: 403 });
  }

  try {
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
  } catch (error) {
    if (isDatabaseUnavailableError(error)) {
      console.warn("Admin users route: Prisma auth database is unavailable.", error);
      return NextResponse.json(
        {
          error: "User accounts are unavailable because the local auth database is offline.",
          code: "USER_DB_UNAVAILABLE",
        },
        { status: 503 },
      );
    }

    console.error("Admin users route failed unexpectedly.", error);
    return NextResponse.json(
      { error: "Failed to load admin users.", code: "ADMIN_USERS_FAILED" },
      { status: 500 },
    );
  }
}
