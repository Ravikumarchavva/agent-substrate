/**
 * Engine authentication helpers.
 * Generates a short-lived JWT signed with ENGINE_JWT_SECRET (= ravi-engine JWT_SECRET)
 * for server-side calls to the ravi-engine API.
 */
import jwt from "jsonwebtoken";

const ENGINE_JWT_SECRET = process.env.ENGINE_JWT_SECRET ?? "";

export function makeEngineToken(): string {
  if (!ENGINE_JWT_SECRET) return "";
  return jwt.sign(
    { sub: "ravi-ui", email: "ui@ravi.local", type: "access" },
    ENGINE_JWT_SECRET,
    { expiresIn: "1h" }
  );
}

export function engineAuthHeader(): HeadersInit {
  const token = makeEngineToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}
