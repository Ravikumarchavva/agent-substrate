/**
 * GET /api/auth/session
 *
 * The one server-verified answer to "who is signed in" for the browser: read from the encrypted session cookie
 * (refreshed when needed). AuthContext uses only this; nothing in the browser reads an auth cookie.
 */
import { bff, userFromAccessToken, withAuthCookies } from "@/lib/session";

export async function GET(req: Request) {
  const result = await bff.getSession(req);
  if (!result) {
    const res = Response.json({ authenticated: false, user: null });
    for (const cookie of bff.clearSession(req)) res.headers.append("Set-Cookie", cookie);
    return res;
  }
  const user = userFromAccessToken(result.session.access_token, result.session.name);
  return withAuthCookies(
    Response.json({
      authenticated: true,
      user: { id: user.id, email: user.email, name: user.name, isAdmin: user.isAdmin, tenantId: user.tenantId },
    }),
    { user, headers: {}, setCookies: result.setCookies },
  );
}
