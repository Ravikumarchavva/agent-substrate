import { NextRequest, NextResponse } from "next/server";
import {
  SESSION_COOKIE,
  type Session,
  config,
  cookieOptions,
  fresh,
  seal,
  unseal,
} from "../../../../lib/session";

// Streams /api/v1/* to the data API with the session's bearer token. Bodies are not buffered.
const FORWARDED = ["accept", "content-type", "if-match", "x-request-id"];

async function forward(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
) {
  if (request.method !== "GET" && request.method !== "HEAD") {
    const origin = request.headers.get("origin");
    if (origin && origin !== config().publicUrl) {
      return NextResponse.json(
        { detail: "Cross-site request refused" },
        { status: 403 },
      );
    }
  }
  const stored = await unseal<Session>(
    request.cookies.get(SESSION_COOKIE)?.value,
  );
  const current = stored ? await fresh(stored) : null;
  if (!current) {
    const response = NextResponse.json(
      {
        type: "https://substrate.dev/problems/unauthorized",
        title: "Unauthorized",
        status: 401,
        detail: "Sign in again",
      },
      { status: 401 },
    );
    response.cookies.delete(SESSION_COOKIE);
    return response;
  }
  const { path } = await context.params;
  const target = new URL(
    `${config().api}/api/v1/${path.map(encodeURIComponent).join("/")}`,
  );
  target.search = request.nextUrl.search;
  const headers = new Headers({
    Authorization: `Bearer ${current.session.access_token}`,
  });
  for (const name of FORWARDED) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  const upstream = await fetch(target, {
    method: request.method,
    headers,
    body:
      request.method === "GET" || request.method === "HEAD"
        ? undefined
        : request.body,
    // @ts-expect-error -- Node fetch needs half-duplex for streamed request bodies.
    duplex: "half",
    cache: "no-store",
    redirect: "manual",
  });
  const response = new NextResponse(upstream.body, {
    status: upstream.status,
    headers: upstream.headers,
  });
  response.headers.delete("set-cookie");
  if (current.changed) {
    response.cookies.set(
      SESSION_COOKIE,
      await seal(current.session),
      cookieOptions(60 * 60 * 10),
    );
  }
  return response;
}

export const GET = forward;
export const POST = forward;
export const PUT = forward;
export const PATCH = forward;
export const DELETE = forward;
