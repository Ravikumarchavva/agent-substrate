/**
 * Catch-all proxy: /api/backend/* -> the Agent API (/api/v1/*)
 *
 * Calls the API with the signed-in user's own Keycloak access token, read from the encrypted session cookie
 * (@substrate/oidc-bff, refreshed here when it is about to expire). The API verifies the token itself and takes the
 * tenant and roles from it. No session -> 401 problem+json; there is no anonymous or service-account fallback.
 */
import { NextRequest } from "next/server";
import { BACKEND_API } from "@/lib/backend";
import { bff, requireAuth, withAuthCookies } from "@/lib/session";
import { streamingDispatcher } from "@/lib/streaming-dispatcher";

// Stream responses (SSE) must not be buffered or statically optimized.
export const runtime = "nodejs";
export const dynamic = "force-dynamic";

async function proxyRequest(req: NextRequest, path: string[]): Promise<Response> {
  const auth = await requireAuth(req);
  if (!auth) return bff.unauthorized(req);

  const targetUrl = `${BACKEND_API}/${path.map(encodeURIComponent).join("/")}${req.nextUrl.search}`;

  const headers = new Headers();
  // Forward safe request headers — deliberately NOT "cookie": this app's
  // own auth/session/OAuth cookies have no meaning to agent-substrate and
  // forwarding them just leaks them into another service's logs/traces.
  for (const [k, v] of req.headers.entries()) {
    const lower = k.toLowerCase();
    if (
      ["content-type", "accept", "x-request-id", "x-base-checksum", "if-none-match"].includes(
        lower,
      )
    ) {
      headers.set(k, v);
    }
  }

  for (const [k, v] of Object.entries(auth.headers)) headers.set(k, v);

  const body =
    req.method === "GET" || req.method === "HEAD" ? undefined : req.body;

  const upstream = await fetch(targetUrl, {
    method: req.method,
    headers,
    body,
    duplex: "half",
    dispatcher: streamingDispatcher,
    // duplex is required by Node's fetch for a streaming request body;
    // dispatcher is a Node/undici extension — neither is in the standard
    // fetch() types, so the whole options object is cast rather than
    // suppressing errors property-by-property (TS anchors the "no overload
    // matches" diagnostic at whichever unknown property comes first, which
    // makes per-line @ts-expect-error comments break silently if the
    // property order ever changes).
  } as RequestInit & { duplex: "half"; dispatcher: unknown });

  // Stream response back as-is (handles SSE, JSON, binary)
  const responseHeaders = Object.fromEntries(upstream.headers.entries());
  if (responseHeaders["content-type"]?.includes("text/event-stream")) {
    // Keep gzip from buffering a live stream (see app/api/chat/route.ts).
    responseHeaders["cache-control"] = "no-cache, no-transform";
    responseHeaders["x-accel-buffering"] = "no";
  }
  return withAuthCookies(
    new Response(upstream.body, {
      status: upstream.status,
      statusText: upstream.statusText,
      headers: responseHeaders,
    }),
    auth,
  );
}

export async function GET(req: NextRequest, { params }: { params: Promise<{ path: string[] }> }) {
  return proxyRequest(req, (await params).path);
}
export async function POST(req: NextRequest, { params }: { params: Promise<{ path: string[] }> }) {
  return proxyRequest(req, (await params).path);
}
export async function PUT(req: NextRequest, { params }: { params: Promise<{ path: string[] }> }) {
  return proxyRequest(req, (await params).path);
}
export async function PATCH(req: NextRequest, { params }: { params: Promise<{ path: string[] }> }) {
  return proxyRequest(req, (await params).path);
}
export async function DELETE(req: NextRequest, { params }: { params: Promise<{ path: string[] }> }) {
  return proxyRequest(req, (await params).path);
}
