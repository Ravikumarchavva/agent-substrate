/**
 * Catch-all proxy: /api/backend/* → agent-substrate
 *
 * Adds an engine-scoped JWT so the engine's auth middleware is satisfied.
 * The JWT is signed with ENGINE_JWT_SECRET (same value as agent-substrate JWT_SECRET).
 * A new token is generated per request (short-lived), minted from the
 * caller's DB-backed session (src/lib/session.ts) — never from a
 * client-editable cookie. No session → 401; there is no anonymous or
 * service-account fallback for user-data routes anymore (see the beta
 * security audit this replaces: the previous unsigned `user_session`
 * cookie let anyone become any user, including an admin, just by editing
 * it in devtools).
 */
import { NextRequest } from "next/server";
import { requireUserAuthHeaderFromRequest } from "@/lib/engine-auth";
import { streamingDispatcher } from "@/lib/streaming-dispatcher";

// Stream responses (SSE) must not be buffered or statically optimized.
export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const BACKEND_URL =
  process.env.BACKEND_API_URL ||
  process.env.NEXT_PUBLIC_API_URL ||
  "http://localhost:8000";

async function proxyRequest(req: NextRequest, path: string[]): Promise<Response> {
  const auth = await requireUserAuthHeaderFromRequest(req);
  if (!auth) {
    return Response.json({ error: "Not authenticated" }, { status: 401 });
  }

  const targetUrl = `${BACKEND_URL}/${path.join("/")}${req.nextUrl.search}`;

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
  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: Object.fromEntries(upstream.headers.entries()),
  });
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
