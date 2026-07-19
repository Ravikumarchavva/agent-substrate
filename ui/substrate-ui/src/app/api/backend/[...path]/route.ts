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
 * workspace storage) is per-person. Otherwise falls back to the fixed
 * service-account token — e.g. for requests with no logged-in user.
 */
import { NextRequest } from "next/server";
import { engineAuthHeader, userAuthHeader, type UserSession } from "@/lib/engine-auth";

function authHeaderFor(req: NextRequest): HeadersInit {
  const raw = req.cookies.get("user_session")?.value;
  if (raw) {
    try {
      const session = JSON.parse(raw) as UserSession;
      if (session.id && session.email) return userAuthHeader(session);
    } catch {
      // Malformed cookie — fall through to the service-account token.
    }
  }
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
    if (["content-type", "accept", "cookie", "x-request-id"].includes(lower)) {
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
    // @ts-expect-error — Node 18 fetch supports duplex for streaming
    duplex: "half",
  });

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
