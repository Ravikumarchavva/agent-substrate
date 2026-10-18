import "server-only";
import { createBff } from "@substrate/oidc-bff";
import { LOGIN_COOKIE, SESSION_COOKIE } from "./session-cookie";

// Backend-for-frontend session (shared library): OIDC tokens live only in an AES-GCM encrypted, httpOnly
// cookie. The browser never sees an access token; route handlers attach it when calling the API.

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

export const bff = createBff(() => {
  const c = config();
  return {
    issuer: c.issuer,
    clientId: c.clientId,
    clientSecret: c.clientSecret,
    sessionSecret: c.secret,
    publicUrl: c.publicUrl,
    sessionCookie: SESSION_COOKIE,
    loginCookie: LOGIN_COOKIE,
  };
});
