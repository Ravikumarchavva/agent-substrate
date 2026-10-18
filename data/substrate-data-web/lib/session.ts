import "server-only";

// Backend-for-frontend session: OIDC tokens live only in an AES-GCM encrypted, httpOnly cookie.
// The browser never sees an access token; route handlers attach it when calling the API.

export const SESSION_COOKIE = "sd_session";
export const LOGIN_COOKIE = "sd_login";
const encoder = new TextEncoder();
const decoder = new TextDecoder();

export type Session = {
  access_token: string;
  refresh_token: string;
  expires_at: number;
  name: string;
};

export type LoginState = {
  state: string;
  verifier: string;
  nonce: string;
  next: string;
};

export function config() {
  const issuer = process.env.SUBSTRATE_DATA_OIDC_ISSUER;
  const secret = process.env.WEB_SESSION_SECRET;
  const clientSecret = process.env.WEB_CLIENT_SECRET;
  if (!issuer || !secret || secret.length < 32 || !clientSecret) {
    throw new Error(
      "Set SUBSTRATE_DATA_OIDC_ISSUER, WEB_CLIENT_SECRET and WEB_SESSION_SECRET (32+ chars)",
    );
  }
  return {
    issuer,
    secret,
    clientId: process.env.WEB_CLIENT_ID || "substrate-data-web",
    clientSecret,
    api: process.env.DATA_API_URL || "http://127.0.0.1:8020",
    // The address users reach the UI at. Redirect URIs and cookies must agree on one origin.
    publicUrl: (process.env.WEB_PUBLIC_URL || "http://127.0.0.1:3011").replace(
      /\/$/,
      "",
    ),
  };
}

async function key(): Promise<CryptoKey> {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    encoder.encode(config().secret),
  );
  return crypto.subtle.importKey("raw", digest, "AES-GCM", false, [
    "encrypt",
    "decrypt",
  ]);
}

function b64url(bytes: Uint8Array): string {
  return Buffer.from(bytes).toString("base64url");
}

export async function seal(value: unknown): Promise<string> {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const data = await crypto.subtle.encrypt(
    { name: "AES-GCM", iv },
    await key(),
    encoder.encode(JSON.stringify(value)),
  );
  return `${b64url(iv)}.${b64url(new Uint8Array(data))}`;
}

export async function unseal<T>(sealed: string | undefined): Promise<T | null> {
  if (!sealed) return null;
  const [iv, data] = sealed.split(".");
  if (!iv || !data) return null;
  try {
    const plain = await crypto.subtle.decrypt(
      { name: "AES-GCM", iv: Buffer.from(iv, "base64url") },
      await key(),
      Buffer.from(data, "base64url"),
    );
    return JSON.parse(decoder.decode(plain)) as T;
  } catch {
    return null; // tampered, expired key or malformed
  }
}

export function randomToken(bytes = 32): string {
  return b64url(crypto.getRandomValues(new Uint8Array(bytes)));
}

export async function challenge(verifier: string): Promise<string> {
  return b64url(
    new Uint8Array(
      await crypto.subtle.digest("SHA-256", encoder.encode(verifier)),
    ),
  );
}

export const cookieOptions = (maxAge: number) => ({
  httpOnly: true,
  secure: process.env.NODE_ENV === "production",
  sameSite: "lax" as const,
  path: "/",
  maxAge,
});

type TokenResponse = {
  access_token: string;
  refresh_token: string;
  expires_in: number;
  refresh_expires_in?: number;
};

export async function tokenRequest(
  form: Record<string, string>,
): Promise<TokenResponse> {
  const { issuer, clientId, clientSecret } = config();
  const response = await fetch(`${issuer}/protocol/openid-connect/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      ...form,
      client_id: clientId,
      client_secret: clientSecret,
    }),
    cache: "no-store",
  });
  if (!response.ok)
    throw new Error(`Token endpoint returned ${response.status}`);
  return response.json();
}

export function sessionFrom(tokens: TokenResponse): Session {
  const claims = JSON.parse(
    Buffer.from(tokens.access_token.split(".")[1], "base64url").toString(),
  );
  return {
    access_token: tokens.access_token,
    refresh_token: tokens.refresh_token,
    expires_at: Date.now() + tokens.expires_in * 1000,
    name: claims.name || claims.preferred_username || "user",
  };
}

/** Refresh shortly before expiry; returns null when the refresh token is no longer valid. */
export async function fresh(
  session: Session,
): Promise<{ session: Session; changed: boolean } | null> {
  if (session.expires_at - Date.now() > 30_000)
    return { session, changed: false };
  try {
    const tokens = await tokenRequest({
      grant_type: "refresh_token",
      refresh_token: session.refresh_token,
    });
    return { session: sessionFrom(tokens), changed: true };
  } catch {
    return null;
  }
}
