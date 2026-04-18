import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";

export function middleware(req: NextRequest) {
  if (process.env.NODE_ENV === "production") {
    return NextResponse.next();
  }

  if (req.nextUrl.hostname !== "localhost") {
    return NextResponse.next();
  }

  const redirectUrl = req.nextUrl.clone();
  redirectUrl.hostname = "127.0.0.1";
  return NextResponse.redirect(redirectUrl);
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|.*\\..*).*)"],
};