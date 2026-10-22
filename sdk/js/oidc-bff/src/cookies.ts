// Cookies on the Web Request/Response APIs. Large values are split across `name`, `name.1`, `name.2`...
// because two Keycloak JWTs can exceed the 4 KB limit of a single cookie.

export const CHUNK_SIZE = 3600;
const MAX_CHUNKS = 8;

export function parseCookies(request: Request): Map<string, string> {
  const jar = new Map<string, string>();
  for (const part of (request.headers.get("cookie") ?? "").split(";")) {
    const index = part.indexOf("=");
    if (index < 1) continue;
    const name = part.slice(0, index).trim();
    if (!jar.has(name)) jar.set(name, part.slice(index + 1).trim());
  }
  return jar;
}

const chunkName = (name: string, index: number) =>
  index === 0 ? name : `${name}.${index}`;

/** The full value stored under `name`, reassembled from its chunks. */
export function readCookie(request: Request, name: string): string | undefined {
  const jar = parseCookies(request);
  let value = jar.get(name);
  if (value === undefined) return undefined;
  for (let index = 1; index < MAX_CHUNKS; index++) {
    const next = jar.get(chunkName(name, index));
    if (next === undefined) break;
    value += next;
  }
  return value;
}

export function hasCookie(request: Request, name: string): boolean {
  return parseCookies(request).has(name);
}

export type CookieOptions = { maxAge: number; secure: boolean };

function serialize(
  name: string,
  value: string,
  options: CookieOptions,
): string {
  const parts = [
    `${name}=${value}`,
    "Path=/",
    "HttpOnly",
    "SameSite=Lax",
    `Max-Age=${options.maxAge}`,
  ];
  if (options.secure) parts.push("Secure");
  return parts.join("; ");
}

/** Set-Cookie header values that store `value` under `name`, and expire leftover chunks of an older, longer value. */
export function setCookies(
  request: Request,
  name: string,
  value: string,
  options: CookieOptions,
): string[] {
  const chunks: string[] = [];
  for (let at = 0; at < value.length; at += CHUNK_SIZE)
    chunks.push(value.slice(at, at + CHUNK_SIZE));
  if (chunks.length > MAX_CHUNKS)
    throw new Error("Session is too large to store in cookies");
  const headers = chunks.map((chunk, index) =>
    serialize(chunkName(name, index), chunk, options),
  );
  return [
    ...headers,
    ...clearCookies(request, name, options.secure, chunks.length),
  ];
}

/** Set-Cookie header values that delete `name` and every chunk of it present in the request. */
export function clearCookies(
  request: Request,
  name: string,
  secure: boolean,
  from = 0,
): string[] {
  const jar = parseCookies(request);
  const headers: string[] = [];
  for (let index = from; index < MAX_CHUNKS; index++) {
    const present = chunkName(name, index);
    if (jar.has(present) || (from === 0 && index === 0))
      headers.push(serialize(present, "", { maxAge: 0, secure }));
  }
  return headers;
}
