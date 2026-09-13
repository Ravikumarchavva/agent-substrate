/**
 * Catch-all proxy: /api/backend/* → agent-substrate
 *
 * Adds an engine-scoped JWT so the engine's auth middleware is satisfied.
 * The JWT is signed with ENGINE_JWT_SECRET (same value as agent-substrate JWT_SECRET).
 * A new token is generated per request (short-lived).
 *
 * If the request carries an httpOnly `user_session` cookie (set at Google
 * OAuth login, see api/auth/google/callback), the token is signed with that
 * real user's id as `sub` so agent-substrate-side scoping (file ownership,
 * workspace storage) is per-person. With no login it falls back to the
 * per-browser `anon_id` cookie, and only to the shared service-account token
 * if even that is missing.
 */
import { NextRequest } from "next/server";
import {
  ANON_COOKIE,
  anonAuthHeader,
  engineAuthHeader,
  userAuthHeader,
  type UserSession,
} from "@/lib/engine-auth";
import { streamingDispatcher } from "@/lib/streaming-dispatcher";

function authHeaderFor(req: NextRequest): HeadersInit {
  const raw = req.cookies.get("user_session")?.value;
  if (raw) {
    try {
      const session = JSON.parse(raw) as UserSession;
      if (session.id && session.email) return userAuthHeader(session);
    } catch {
      // Malformed cookie — fall through to a weaker identity.
    }
  }
  // No login: a per-browser anonymous id (planted by middleware.ts) keeps this
  // visitor's workspace and quota separate from every other visitor's.
  const anonId = req.cookies.get(ANON_COOKIE)?.value;
  if (anonId) return anonAuthHeader(anonId);
  return engineAuthHeader();
}

// Stream responses (SSE) must not be buffered or statically optimized.
export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const BACKEND_URL =
  process.env.BACKEND_API_URL ||
  process.env.NEXT_PUBLIC_API_URL ||
  "http://localhost:8000";

async function proxyRequest(req: NextRequest, path: string[]): Promise<Response> {
  const targetUrl = `${BACKEND_URL}/${path.join("/")}${req.nextUrl.search}`;

  const headers = new Headers();
  // Forward safe request headers
  for (const [k, v] of req.headers.entries()) {
    const lower = k.toLowerCase();
    if (
      ["content-type", "accept", "cookie", "x-request-id", "x-base-checksum", "if-none-match"].includes(
        lower,
      )
    ) {
      headers.set(k, v);
    }
  }

  // Inject engine JWT — per-user if a session cookie is present, else
  // the fixed service-account token.
  for (const [k, v] of Object.entries(authHeaderFor(req))) headers.set(k, v);

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
