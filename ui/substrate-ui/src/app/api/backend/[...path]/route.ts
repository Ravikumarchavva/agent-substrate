/**
 * Catch-all proxy: /api/backend/* → ravi-engine
 *
 * Adds an engine-scoped JWT so the engine's auth middleware is satisfied.
 * The JWT is signed with ENGINE_JWT_SECRET (same value as ravi-engine JWT_SECRET).
 * A new token is generated per request (short-lived, signed as service account).
 */
import { NextRequest } from "next/server";
import { engineAuthHeader } from "@/lib/engine-auth";

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

  // Inject engine JWT
  for (const [k, v] of Object.entries(engineAuthHeader())) headers.set(k, v);

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
