import { NextRequest, NextResponse } from "next/server";
import { bff, config } from "../../../../lib/session";

// Streams /api/v1/* to the data API with the session's bearer token. Bodies are not buffered.
const FORWARDED = ["accept", "content-type", "if-match", "x-request-id"];

async function forward(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
) {
  if (!bff.sameOrigin(request)) {
    return NextResponse.json(
      { detail: "Cross-site request refused" },
      { status: 403 },
    );
  }
  const bearer = await bff.bearerFor(request);
  if (!bearer) return bff.unauthorized(request);
  const { path } = await context.params;
  const target = new URL(
    `${config().api}/api/v1/${path.map(encodeURIComponent).join("/")}`,
  );
  target.search = request.nextUrl.search;
  const headers = new Headers({
    Authorization: `Bearer ${bearer.token}`,
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
  return bff.withCookies(response, bearer.setCookies);
}

export const GET = forward;
export const POST = forward;
export const PUT = forward;
export const PATCH = forward;
export const DELETE = forward;
