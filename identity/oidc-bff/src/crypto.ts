// AES-GCM sealing of cookie payloads, PKCE and random tokens. Web Crypto only (no Node-specific APIs).

const encoder = new TextEncoder();
const decoder = new TextDecoder();

export function b64url(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary)
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
}

export function fromB64url(text: string): Uint8Array {
  const padded = text.replace(/-/g, "+").replace(/_/g, "/");
  const binary = atob(padded + "=".repeat((4 - (padded.length % 4)) % 4));
  return Uint8Array.from(binary, (c) => c.charCodeAt(0));
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

/** An AES-256-GCM key derived (HKDF) per purpose, so a login cookie can never be replayed as a session. */
async function key(secret: string, purpose: string): Promise<CryptoKey> {
  const material = await crypto.subtle.importKey(
    "raw",
    encoder.encode(secret),
    "HKDF",
    false,
    ["deriveKey"],
  );
  return crypto.subtle.deriveKey(
    {
      name: "HKDF",
      hash: "SHA-256",
      salt: encoder.encode("substrate-oidc-bff"),
      info: encoder.encode(purpose),
    },
    material,
    { name: "AES-GCM", length: 256 },
    false,
    ["encrypt", "decrypt"],
  );
}

/** Encrypt and authenticate `value`; it expires `ttlSeconds` from `now` regardless of the cookie's own lifetime. */
export async function seal(
  secret: string,
  purpose: string,
  value: unknown,
  ttlSeconds: number,
  now = Date.now(),
): Promise<string> {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const payload = JSON.stringify({ v: value, exp: now + ttlSeconds * 1000 });
  const data = await crypto.subtle.encrypt(
    { name: "AES-GCM", iv, additionalData: encoder.encode(purpose) },
    await key(secret, purpose),
    encoder.encode(payload),
  );
  return `${b64url(iv)}.${b64url(new Uint8Array(data))}`;
}

/** The sealed value, or null when missing, tampered with, sealed for another purpose, or expired. */
export async function unseal<T>(
  secret: string,
  purpose: string,
  sealed: string | undefined | null,
  now = Date.now(),
): Promise<T | null> {
  if (!sealed) return null;
  const [iv, data, extra] = sealed.split(".");
  if (!iv || !data || extra !== undefined) return null;
  try {
    const plain = await crypto.subtle.decrypt(
      {
        name: "AES-GCM",
        iv: fromB64url(iv) as BufferSource,
        additionalData: encoder.encode(purpose),
      },
      await key(secret, purpose),
      fromB64url(data) as BufferSource,
    );
    const parsed = JSON.parse(decoder.decode(plain)) as { v: T; exp: number };
    return typeof parsed.exp === "number" && parsed.exp > now ? parsed.v : null;
  } catch {
    return null;
  }
}

/** Claims of a JWT received directly from the token endpoint over TLS (OIDC Core 3.1.3.7: no signature check needed). */
export function claimsOf(jwt: string): Record<string, unknown> {
  try {
    const payload = jwt.split(".")[1];
    return payload
      ? (JSON.parse(decoder.decode(fromB64url(payload))) as Record<
          string,
          unknown
        >)
      : {};
  } catch {
    return {};
  }
}
